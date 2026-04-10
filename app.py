import os
import json
import time
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import threading

from database import db, Prefeitura, Pedido, ItemPedido, Produto, Fornecedor
from database import ListaConsolidada, ItemConsolidado, Cotacao, ValeEntrega
from database import ConfiguracaoEmail, ConfiguracaoGeral, Empresa
from email_reader import ler_emails_novos, testar_conexao, detectar_servidor
from ai_parser import (extrair_pedido_com_ia, gerar_mensagem_whatsapp,
                       gerar_mensagem_fornecedor, sugerir_categoria_produto,
                       sugerir_destino_produto, email_e_pedido,
                       extrair_empresa_prefeitura_do_assunto, _norm,
                       preparar_conteudo_email)
from pdf_generator import gerar_vale_entrega, gerar_lista_compras

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VALES_DIR = os.path.join(BASE_DIR, 'vales_pdf')
os.makedirs(VALES_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'sistema-pedidos-hortifruti-2024')

# Suporta PostgreSQL (Render) e SQLite (local)
_database_url = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(BASE_DIR, "pedidos.db")}')
if _database_url.startswith('postgres://'):
    _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)


def _migrar_banco():
    """Executa migrações compatíveis com SQLite e PostgreSQL."""
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        tabelas = inspector.get_table_names()

        if 'fornecedores' in tabelas:
            cols = [c['name'] for c in inspector.get_columns('fornecedores')]
            if 'categorias' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE fornecedores ADD COLUMN categorias TEXT DEFAULT ''"))
                    conn.commit()

        if 'pedidos' in tabelas:
            cols = [c['name'] for c in inspector.get_columns('pedidos')]
            if 'empresa_id' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text('ALTER TABLE pedidos ADD COLUMN empresa_id INTEGER'))
                    conn.commit()

        if 'prefeituras' in tabelas:
            cols = [c['name'] for c in inspector.get_columns('prefeituras')]
            if 'empresa_id' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text('ALTER TABLE prefeituras ADD COLUMN empresa_id INTEGER'))
                    conn.commit()
    except Exception as e:
        print(f'[MIGRAÇÃO] Aviso: {e}')


def _inicializar_dados():
    """Cria dados padrão se não existirem."""
    db.create_all()
    _migrar_banco()

    if not ConfiguracaoGeral.query.first():
        db.session.add(ConfiguracaoGeral(nome_empresa='Profeta e Fortumel', proximo_vale=0))
        db.session.commit()

    if not Fornecedor.query.filter(db.func.lower(Fornecedor.nome) == 'thales').first():
        db.session.add(Fornecedor(
            nome='Thales',
            telefone_whatsapp='+55 31 99957-2011',
            tipo='ambos',
            categorias='hortifruti,carne'
        ))
        db.session.commit()

    if not Fornecedor.query.filter(db.func.lower(Fornecedor.nome) == 'martminas').first():
        db.session.add(Fornecedor(
            nome='MartMinas',
            telefone_whatsapp='+55 31 99830-7940',
            tipo='local',
            categorias='genero,limpeza,carne'
        ))
        db.session.commit()

    # Cria as 3 empresas se não existirem
    empresas_dados = [
        'Profeta Distribuidora e Servicos',
        'Eros Distribuidora',
        'Fortumel Produtos'
    ]
    empresas_map = {}
    for nome_emp in empresas_dados:
        e = Empresa.query.filter(db.func.lower(Empresa.nome) == nome_emp.lower()).first()
        if not e:
            e = Empresa(nome=nome_emp, ativa=True)
            db.session.add(e)
            db.session.flush()
        empresas_map[nome_emp] = e
    db.session.commit()

    # Recarrega o mapa após commit
    for nome_emp in empresas_dados:
        empresas_map[nome_emp] = Empresa.query.filter(db.func.lower(Empresa.nome) == nome_emp.lower()).first()

    # Prefeituras da Profeta
    profeta = empresas_map.get('Profeta Distribuidora e Servicos')
    prefeituras_profeta = [
        'Prefeitura Municipal de Casa Grande',
        'Prefeitura Municipal de Queluzito',
        'Prefeitura Municipal de Resende Costa',
        'Prefeitura Municipal de Senhora dos Remedios',
        'Prefeitura Municipal de Entre Rios de Minas',
        'Prefeitura Municipal de Itavera',
        'Prefeitura Municipal de Sao Bras do Suacui',
    ]
    for nome_pref in prefeituras_profeta:
        existe = Prefeitura.query.filter(db.func.lower(Prefeitura.nome) == nome_pref.lower()).first()
        if not existe and profeta:
            db.session.add(Prefeitura(
                nome=nome_pref, email='', endereco='', contato='',
                empresa_id=profeta.id, ativa=True
            ))

    # Prefeituras da Eros
    eros = empresas_map.get('Eros Distribuidora')
    prefeituras_eros = [
        'Prefeitura Municipal de Caranaiba',
    ]
    for nome_pref in prefeituras_eros:
        existe = Prefeitura.query.filter(db.func.lower(Prefeitura.nome) == nome_pref.lower()).first()
        if not existe and eros:
            db.session.add(Prefeitura(
                nome=nome_pref, email='', endereco='', contato='',
                empresa_id=eros.id, ativa=True
            ))

    db.session.commit()


# Inicializa ao importar (necessário para gunicorn/Render)
with app.app_context():
    _inicializar_dados()


def get_config():
    cfg = ConfiguracaoGeral.query.first()
    if not cfg:
        cfg = ConfiguracaoGeral()
        db.session.add(cfg)
        db.session.commit()
    return cfg


def get_email_config():
    return ConfiguracaoEmail.query.first()

def get_email_configs():
    return ConfiguracaoEmail.query.filter_by(ativa=True).all()


# ============================================================
# ROTAS PRINCIPAIS
# ============================================================

@app.route('/')
def dashboard():
    pedidos_novos = Pedido.query.filter_by(status='novo').count()
    pedidos_processados = Pedido.query.filter_by(status='processado').count()
    pedidos_comprado = Pedido.query.filter_by(status='comprado').count()
    total_pedidos = Pedido.query.count()
    prefeituras_ativas = Prefeitura.query.filter_by(ativa=True).count()
    lista_atual = ListaConsolidada.query.filter(
        ListaConsolidada.status.in_(['aberta', 'cotacao'])
    ).order_by(ListaConsolidada.data_criacao.desc()).first()

    pedidos_recentes = Pedido.query.order_by(Pedido.criado_em.desc()).limit(5).all()

    return render_template('dashboard.html',
        pedidos_novos=pedidos_novos,
        pedidos_processados=pedidos_processados,
        pedidos_comprado=pedidos_comprado,
        total_pedidos=total_pedidos,
        prefeituras_ativas=prefeituras_ativas,
        lista_atual=lista_atual,
        pedidos_recentes=pedidos_recentes,
        config=get_config()
    )


# ============================================================
# EMAILS
# ============================================================

@app.route('/emails')
def emails():
    prefeituras = Prefeitura.query.filter_by(ativa=True).all()
    pedidos = Pedido.query.order_by(Pedido.criado_em.desc()).limit(50).all()
    email_cfgs = get_email_configs()
    return render_template('emails.html',
        prefeituras=prefeituras,
        pedidos=pedidos,
        email_cfgs=email_cfgs,
        config=get_config()
    )


@app.route('/api/webhook/email', methods=['POST'])
def webhook_email():
    """
    Endpoint para receber emails via Google Apps Script.
    O Apps Script lê o Gmail e envia o conteúdo aqui via HTTP POST.
    Assim não precisamos de IMAP — funciona em qualquer hospedagem.
    """
    # Verifica token de segurança
    token = request.headers.get('X-Webhook-Token') or request.json.get('token', '')
    cfg = get_config()
    webhook_token = cfg.webhook_token if hasattr(cfg, 'webhook_token') and cfg.webhook_token else 'monica2024'
    if token != webhook_token:
        return jsonify({'erro': 'Token inválido'}), 401

    data = request.json or {}
    emails_recebidos = data.get('emails', [])
    if not emails_recebidos:
        return jsonify({'mensagem': 'Nenhum email recebido', 'total': 0})

    api_key = cfg.anthropic_api_key
    emails_existentes = {p.email_id for p in Pedido.query.filter(Pedido.email_id.isnot(None)).all()}
    todas_prefeituras = Prefeitura.query.filter_by(ativa=True).all()
    todas_empresas = Empresa.query.filter_by(ativa=True).all()

    novos_pedidos = []
    for email_data in emails_recebidos:
        # Converte anexos base64 para bytes (enviados pelo Apps Script)
        import base64 as _base64
        for anexo in email_data.get('anexos', []):
            if 'dados_base64' in anexo and not anexo.get('dados'):
                try:
                    anexo['dados'] = _base64.b64decode(anexo['dados_base64'])
                except Exception:
                    anexo['dados'] = b''
        email_id = email_data.get('email_id', '')
        if email_id and email_id in emails_existentes:
            continue  # já processado

        assunto = email_data.get('assunto', '')
        email_rem = email_data.get('email_remetente', '').lower()

        prefeitura, empresa = extrair_empresa_prefeitura_do_assunto(
            assunto, todas_prefeituras, todas_empresas)

        if not prefeitura and not empresa:
            e_pedido, motivo = email_e_pedido(email_data)
            if not e_pedido and ' - ' not in assunto:
                continue

        resultado_ia = {}
        if api_key:
            resultado_ia = extrair_pedido_com_ia(email_data, api_key)

        if not prefeitura:
            prefeitura = _encontrar_prefeitura_webhook(resultado_ia.get('prefeitura_nome', ''), todas_prefeituras)
        if not empresa:
            empresa = _encontrar_empresa_webhook(resultado_ia.get('empresa_nome', ''), todas_empresas)
        if prefeitura and not empresa and prefeitura.empresa_id:
            empresa = Empresa.query.get(prefeitura.empresa_id)

        if not prefeitura:
            nome_pref = resultado_ia.get('prefeitura_nome') or assunto or f'Novo: {email_rem}'
            prefeitura = Prefeitura(
                nome=nome_pref, email=email_rem,
                endereco='', contato='',
                empresa_id=empresa.id if empresa else None
            )
            db.session.add(prefeitura)
            db.session.flush()
            todas_prefeituras.append(prefeitura)

        if empresa and not prefeitura.empresa_id:
            prefeitura.empresa_id = empresa.id

        pedido = Pedido(
            prefeitura_id=prefeitura.id,
            empresa_id=empresa.id if empresa else None,
            assunto_email=assunto,
            email_id=email_id or None,
            data_recebimento=datetime.now(),
            email_remetente=email_rem,
            corpo_email=preparar_conteudo_email(email_data),
            data_entrega=resultado_ia.get('data_entrega'),
            observacoes=resultado_ia.get('observacoes'),
            status='novo'
        )
        db.session.add(pedido)
        db.session.flush()

        for item_data in resultado_ia.get('itens', []):
            produto_nome = item_data.get('produto', '').strip()
            if not produto_nome:
                continue
            destino = sugerir_destino_produto(produto_nome, api_key)
            db.session.add(ItemPedido(
                pedido_id=pedido.id,
                produto=produto_nome,
                quantidade=float(item_data.get('quantidade', 0)),
                unidade=item_data.get('unidade', 'kg'),
                observacao=item_data.get('observacao', ''),
                destino=destino
            ))

        novos_pedidos.append({'prefeitura': prefeitura.nome, 'itens': len(resultado_ia.get('itens', [])), 'erro_ia': resultado_ia.get('erro', '')})

    db.session.commit()
    return jsonify({'mensagem': f'{len(novos_pedidos)} pedido(s) importado(s)', 'total': len(novos_pedidos), 'pedidos': novos_pedidos})


def _encontrar_prefeitura_webhook(nome_ia, todas_prefeituras):
    if not nome_ia:
        return None
    nome_norm = _norm(nome_ia)
    _SW = {'prefeitura', 'municipal', 'secretaria', 'municipio'}
    melhor, melhor_score = None, 0
    for p in todas_prefeituras:
        kws = [_norm(w) for w in p.nome.split() if len(w) >= 3 and _norm(w) not in _SW]
        score = sum(1 for kw in kws if kw in nome_norm)
        if score > melhor_score:
            melhor_score, melhor = score, p
    return melhor if melhor_score >= 1 else None


def _encontrar_empresa_webhook(nome_ia, todas_empresas):
    if not nome_ia:
        return None
    nome_norm = _norm(nome_ia)
    for e in todas_empresas:
        if any(_norm(w) in nome_norm for w in e.nome.split() if len(w) >= 3):
            return e
    return None


@app.route('/api/emails/buscar', methods=['POST'])
def buscar_emails():
    email_cfgs = get_email_configs()
    if not email_cfgs:
        return jsonify({'erro': 'Configure ao menos um email nas configurações primeiro.'}), 400

    cfg = get_config()
    api_key = cfg.anthropic_api_key
    emails_existentes = {p.email_id for p in Pedido.query.filter(Pedido.email_id.isnot(None)).all()}
    prefeituras_emails = {p.email.lower(): p for p in Prefeitura.query.filter_by(ativa=True).all()}

    # Lê emails de TODAS as contas configuradas
    todos_emails = []
    erros = []
    for ecfg in email_cfgs:
        if not ecfg.email_address or not ecfg.email_password:
            continue
        emails_lidos, erro = ler_emails_novos(
            ecfg.email_address, ecfg.email_password,
            ecfg.imap_server, ecfg.imap_port, emails_existentes
        )
        if erro:
            erros.append(f'{ecfg.email_address}: {erro}')
        else:
            todos_emails.extend(emails_lidos)

    if not todos_emails:
        msg = 'Nenhum email novo encontrado.'
        if erros:
            msg += ' Erros: ' + ' | '.join(erros)
        return jsonify({'mensagem': msg, 'total': 0, 'erros': erros})

    novos_pedidos = []
    ignorados = []

    # Carrega todas as prefeituras e empresas para matching por nome
    todas_prefeituras = Prefeitura.query.filter_by(ativa=True).all()
    todas_empresas = Empresa.query.filter_by(ativa=True).all()

    _STOPWORDS_PREF = {'prefeitura', 'municipal', 'secretaria', 'municipio', 'municipais'}

    def _encontrar_prefeitura_por_nome(nome_ia: str):
        """Busca prefeitura cadastrada pelo nome extraído pela IA (com normalização de acentos)."""
        if not nome_ia:
            return None
        nome_norm = _norm(nome_ia)
        melhor = None
        melhor_score = 0
        for p in todas_prefeituras:
            keywords = [_norm(w) for w in p.nome.split()
                        if len(w) >= 3 and _norm(w) not in _STOPWORDS_PREF]
            score = sum(1 for kw in keywords if kw in nome_norm)
            if score > melhor_score:
                melhor_score = score
                melhor = p
        return melhor if melhor_score >= 1 else None

    def _encontrar_empresa_por_nome(nome_ia: str):
        """Busca empresa cadastrada pelo nome extraído pela IA (com normalização de acentos)."""
        if not nome_ia:
            return None
        nome_norm = _norm(nome_ia)
        for e in todas_empresas:
            palavras = [_norm(w) for w in e.nome.split() if len(w) >= 3]
            if any(p in nome_norm for p in palavras):
                return e
        return None

    for email_data in todos_emails:
        email_rem = email_data.get('email_remetente', '').lower()
        assunto = email_data.get('assunto', '')

        # PASSO 1: tenta identificar pelo assunto (rápido, sem IA)
        # A funcionária coloca no assunto: "Cidade - Empresa"
        prefeitura, empresa = extrair_empresa_prefeitura_do_assunto(
            assunto, todas_prefeituras, todas_empresas
        )

        # PASSO 2: se não identificou pelo assunto, aplica filtro de relevância
        # Se ao menos um (cidade ou empresa) foi identificado no assunto → é pedido válido
        # Se nada foi identificado → aplica filtro (pode ser spam/outro email)
        if not prefeitura and not empresa:
            e_pedido, motivo = email_e_pedido(email_data)
            if not e_pedido:
                # Última tentativa: verifica se o assunto tem o formato "X - Y"
                # que indica que a funcionária enviou (padrão: "Cidade - Empresa")
                if ' - ' not in assunto:
                    ignorados.append({
                        'remetente': email_rem,
                        'assunto': assunto,
                        'motivo': motivo
                    })
                    continue
                # Tem " - " no assunto: aceita como pedido e deixa a IA tentar identificar

        # PASSO 3: usa IA para extrair os ITENS do pedido
        resultado_ia = {}
        if api_key:
            resultado_ia = extrair_pedido_com_ia(email_data, api_key)

        # Se o assunto não identificou, tenta pela IA como fallback
        if not prefeitura:
            prefeitura = _encontrar_prefeitura_por_nome(resultado_ia.get('prefeitura_nome', ''))
        if not empresa:
            empresa = _encontrar_empresa_por_nome(resultado_ia.get('empresa_nome', ''))

        # Se encontrou prefeitura mas não empresa, usa o vínculo fixo do contrato
        if prefeitura and not empresa and prefeitura.empresa_id:
            empresa = Empresa.query.get(prefeitura.empresa_id)

        # Se ainda não encontrou prefeitura, cria nova
        if not prefeitura:
            nome_pref = resultado_ia.get('prefeitura_nome') or assunto or f'Novo: {email_rem}'
            prefeitura = Prefeitura(
                nome=nome_pref,
                email=email_rem,
                endereco=resultado_ia.get('prefeitura_endereco', '') or '',
                contato=resultado_ia.get('prefeitura_contato', '') or '',
                empresa_id=empresa.id if empresa else None
            )
            db.session.add(prefeitura)
            db.session.flush()
            todas_prefeituras.append(prefeitura)

        # Atualiza empresa na prefeitura se encontrou agora e ela não tinha
        if empresa and not prefeitura.empresa_id:
            prefeitura.empresa_id = empresa.id

        # Monta corpo completo para conferência
        corpo = email_data.get('texto_corpo', '') or ''
        anexos_nomes = [a.get('nome', '') for a in email_data.get('anexos', []) if a.get('nome')]
        if anexos_nomes:
            corpo += '\n\n[Anexos: ' + ', '.join(anexos_nomes) + ']'

        pedido = Pedido(
            prefeitura_id=prefeitura.id,
            empresa_id=empresa.id if empresa else None,
            assunto_email=email_data.get('assunto', ''),
            email_id=email_data.get('email_id'),
            data_recebimento=email_data.get('data_recebimento'),
            email_remetente=email_data.get('email_remetente', ''),
            corpo_email=corpo.strip(),
            data_entrega=resultado_ia.get('data_entrega'),
            observacoes=resultado_ia.get('observacoes'),
            status='novo'
        )
        db.session.add(pedido)
        db.session.flush()

        for item_data in resultado_ia.get('itens', []):
            produto_nome = item_data.get('produto', '').strip()
            if not produto_nome:
                continue

            destino = sugerir_destino_produto(produto_nome, api_key)

            item = ItemPedido(
                pedido_id=pedido.id,
                produto=produto_nome,
                quantidade=float(item_data.get('quantidade', 0)),
                unidade=item_data.get('unidade', 'kg'),
                observacao=item_data.get('observacao', ''),
                destino=destino
            )
            db.session.add(item)

            # Cadastra produto se não existir
            prod_existente = Produto.query.filter(
                db.func.lower(Produto.nome) == produto_nome.lower()
            ).first()
            if not prod_existente:
                novo_prod = Produto(nome=produto_nome, destino_padrao=destino)
                db.session.add(novo_prod)

        novos_pedidos.append({
            'prefeitura': prefeitura.nome,
            'assunto': pedido.assunto_email,
            'itens': len(resultado_ia.get('itens', [])),
            'erro_ia': resultado_ia.get('erro')
        })

    db.session.commit()

    msg = f'{len(novos_pedidos)} novo(s) pedido(s) importado(s).'
    if ignorados:
        msg += f' {len(ignorados)} email(s) ignorado(s) por não serem pedidos.'

    return jsonify({
        'mensagem': msg,
        'total': len(novos_pedidos),
        'pedidos': novos_pedidos,
        'ignorados': len(ignorados),
        'erros': erros
    })


@app.route('/api/pedido/<int:pedido_id>/email', methods=['GET'])
def ver_email_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    return jsonify({
        'assunto': pedido.assunto_email or '',
        'remetente': pedido.email_remetente or '',
        'data': pedido.data_recebimento.strftime('%d/%m/%Y às %H:%M') if pedido.data_recebimento else '',
        'corpo': pedido.corpo_email or '(sem conteúdo salvo)',
        'prefeitura': pedido.prefeitura.nome if pedido.prefeitura else ''
    })


@app.route('/api/pedido/<int:pedido_id>/itens', methods=['GET'])
def get_itens_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    return jsonify({
        'pedido': pedido.to_dict(),
        'itens': [i.to_dict() for i in pedido.itens]
    })


@app.route('/api/pedido/<int:pedido_id>/item', methods=['POST'])
def adicionar_item_pedido(pedido_id):
    data = request.json
    item = ItemPedido(
        pedido_id=pedido_id,
        produto=data['produto'],
        quantidade=float(data['quantidade']),
        unidade=data.get('unidade', 'kg'),
        observacao=data.get('observacao', ''),
        destino=data.get('destino', 'local')
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict())


@app.route('/api/pedido/item/<int:item_id>', methods=['PUT', 'DELETE'])
def item_pedido(item_id):
    item = ItemPedido.query.get_or_404(item_id)
    if request.method == 'DELETE':
        db.session.delete(item)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.json
    item.produto = data.get('produto', item.produto)
    item.quantidade = float(data.get('quantidade', item.quantidade))
    item.unidade = data.get('unidade', item.unidade)
    item.observacao = data.get('observacao', item.observacao)
    item.destino = data.get('destino', item.destino)
    db.session.commit()
    return jsonify(item.to_dict())


# ============================================================
# LISTA CONSOLIDADA
# ============================================================

@app.route('/compras')
def compras():
    listas = ListaConsolidada.query.order_by(ListaConsolidada.data_criacao.desc()).limit(10).all()
    lista_atual = listas[0] if listas else None
    fornecedores = Fornecedor.query.filter_by(ativo=True).all()
    return render_template('compras.html',
        listas=listas,
        lista_atual=lista_atual,
        fornecedores=fornecedores,
        config=get_config()
    )


@app.route('/api/lista/consolidar', methods=['POST'])
def consolidar_pedidos():
    """Consolida todos os pedidos com status 'novo' ou 'processado' em uma lista."""
    pedidos = Pedido.query.filter(Pedido.status.in_(['novo', 'processado'])).all()

    if not pedidos:
        return jsonify({'erro': 'Nenhum pedido para consolidar.'}), 400

    # Agrupa por produto
    consolidado = {}
    for pedido in pedidos:
        for item in pedido.itens:
            chave = item.produto.lower()
            if chave not in consolidado:
                consolidado[chave] = {
                    'produto': item.produto,
                    'quantidade_total': 0,
                    'unidade': item.unidade,
                    'destino': item.destino
                }
            consolidado[chave]['quantidade_total'] += item.quantidade

    if not consolidado:
        return jsonify({'erro': 'Pedidos não têm itens.'}), 400

    semana = date.today().strftime('%Y-S%W')
    lista = ListaConsolidada(semana_ref=semana, status='aberta')
    db.session.add(lista)
    db.session.flush()

    for dados in consolidado.values():
        item_c = ItemConsolidado(
            lista_id=lista.id,
            produto=dados['produto'],
            quantidade_total=dados['quantidade_total'],
            unidade=dados['unidade'],
            destino=dados['destino']
        )
        db.session.add(item_c)

    # Marca pedidos como processados
    for pedido in pedidos:
        pedido.status = 'processado'

    db.session.commit()
    return jsonify({'mensagem': 'Lista consolidada com sucesso!', 'lista_id': lista.id})


@app.route('/api/lista/<int:lista_id>', methods=['GET'])
def get_lista(lista_id):
    lista = ListaConsolidada.query.get_or_404(lista_id)
    itens = [i.to_dict() for i in lista.itens]
    ceasa = [i for i in itens if i['destino'] == 'ceasa']
    local = [i for i in itens if i['destino'] != 'ceasa']
    return jsonify({
        'lista': lista.to_dict(),
        'itens': itens,
        'ceasa': ceasa,
        'local': local
    })


@app.route('/api/lista/<int:lista_id>/whatsapp', methods=['GET'])
def gerar_whatsapp(lista_id):
    lista = ListaConsolidada.query.get_or_404(lista_id)
    tipo = request.args.get('tipo', 'todos')  # ceasa, local, todos
    cfg = get_config()

    if tipo == 'ceasa':
        itens = [i for i in lista.itens if i.destino == 'ceasa']
    elif tipo == 'local':
        itens = [i for i in lista.itens if i.destino != 'ceasa']
    else:
        itens = lista.itens

    itens_dict = [i.to_dict() for i in itens]
    mensagem = gerar_mensagem_whatsapp(itens_dict, cfg.nome_empresa)
    return jsonify({'mensagem': mensagem, 'total_itens': len(itens_dict)})


@app.route('/api/lista/<int:lista_id>/cotacao', methods=['POST'])
def salvar_cotacao(lista_id):
    data = request.json
    item_id = data.get('item_id')
    fornecedor_id = data.get('fornecedor_id')
    preco = float(data.get('preco', 0))

    # Remove cotação anterior do mesmo fornecedor para o mesmo item
    Cotacao.query.filter_by(
        item_consolidado_id=item_id,
        fornecedor_id=fornecedor_id
    ).delete()

    cotacao = Cotacao(
        item_consolidado_id=item_id,
        fornecedor_id=fornecedor_id,
        preco=preco,
        observacao=data.get('observacao', '')
    )
    db.session.add(cotacao)

    # Atualiza melhor preço do item
    item = ItemConsolidado.query.get(item_id)
    if item:
        todas_cotacoes = Cotacao.query.filter_by(item_consolidado_id=item_id).all()
        melhor = min(todas_cotacoes, key=lambda c: c.preco)
        item.melhor_preco = melhor.preco
        item.melhor_fornecedor = melhor.fornecedor.nome if melhor.fornecedor else ''
        item.total_valor = item.quantidade_total * melhor.preco

        # Recalcula total da lista
        lista = ListaConsolidada.query.get(lista_id)
        if lista:
            total = sum(i.total_valor or 0 for i in lista.itens)
            lista.total_estimado = total
            lista.status = 'cotacao'

    db.session.commit()
    return jsonify({'ok': True, 'melhor_preco': item.melhor_preco if item else None})


@app.route('/api/lista/<int:lista_id>/pdf', methods=['GET'])
def baixar_lista_pdf(lista_id):
    lista = ListaConsolidada.query.get_or_404(lista_id)
    cfg = get_config()

    itens_ceasa = [i.to_dict() for i in lista.itens if i.destino == 'ceasa']
    itens_local = [i.to_dict() for i in lista.itens if i.destino != 'ceasa']

    nome_arquivo = os.path.join(VALES_DIR, f'lista_compras_{lista_id}.pdf')
    gerar_lista_compras(itens_ceasa, itens_local, cfg.nome_empresa, nome_arquivo)

    return send_file(nome_arquivo, as_attachment=True,
                     download_name=f'lista_compras_{lista.semana_ref}.pdf')


# ============================================================
# VALES DE ENTREGA
# ============================================================

@app.route('/vales')
def vales():
    pedidos = Pedido.query.filter(
        Pedido.status.in_(['processado', 'comprado', 'entregue'])
    ).order_by(Pedido.criado_em.desc()).all()
    vales_list = ValeEntrega.query.order_by(ValeEntrega.data_emissao.desc()).limit(30).all()
    return render_template('vales.html',
        pedidos=pedidos,
        vales=vales_list,
        config=get_config()
    )


@app.route('/api/vale/gerar/<int:pedido_id>', methods=['POST'])
def gerar_vale(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    cfg = get_config()

    # Gera número do vale
    cfg.proximo_vale += 1
    numero_vale = f'V{cfg.proximo_vale:05d}/{date.today().year}'

    # Cria ou atualiza vale
    vale = pedido.vale
    if not vale:
        vale = ValeEntrega(pedido_id=pedido_id)
        db.session.add(vale)

    vale.numero_vale = numero_vale
    vale.data_emissao = datetime.now()

    # Calcula valor total se tiver preços
    total = 0
    for item in pedido.itens:
        # Tenta buscar preço da lista consolidada
        item_c = ItemConsolidado.query.filter(
            db.func.lower(ItemConsolidado.produto) == item.produto.lower()
        ).order_by(ItemConsolidado.id.desc()).first()
        if item_c and item_c.melhor_preco:
            item.preco_unit = item_c.melhor_preco
            total += item.quantidade * item_c.melhor_preco

    vale.valor_total = total if total > 0 else None

    db.session.flush()

    # Gera PDF
    nome_arquivo = f'vale_{pedido_id}_{numero_vale.replace("/", "-")}.pdf'
    caminho = os.path.join(VALES_DIR, nome_arquivo)
    gerar_vale_entrega(pedido, pedido.itens, cfg.nome_empresa, cfg.telefone, caminho, empresa=pedido.empresa)
    vale.arquivo_pdf = nome_arquivo

    db.session.commit()
    return jsonify({'ok': True, 'numero_vale': numero_vale, 'arquivo': nome_arquivo})


@app.route('/api/vale/download/<int:vale_id>')
def download_vale(vale_id):
    vale = ValeEntrega.query.get_or_404(vale_id)
    caminho = os.path.join(VALES_DIR, vale.arquivo_pdf)
    if not os.path.exists(caminho):
        return jsonify({'erro': 'Arquivo não encontrado.'}), 404
    return send_file(caminho, as_attachment=True,
                     download_name=f'vale_{vale.numero_vale.replace("/", "-")}.pdf')


# ============================================================
# PREFEITURAS
# ============================================================

@app.route('/prefeituras')
def prefeituras():
    lista = Prefeitura.query.order_by(Prefeitura.nome).all()
    empresas_lista = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()
    return render_template('prefeituras.html', prefeituras=lista, empresas=empresas_lista, config=get_config())


@app.route('/api/prefeitura', methods=['POST'])
def criar_prefeitura():
    data = request.json
    pref = Prefeitura(
        nome=data['nome'],
        email=data['email'],
        contato=data.get('contato', ''),
        telefone=data.get('telefone', ''),
        endereco=data.get('endereco', ''),
        numero_contrato=data.get('numero_contrato', ''),
        empresa_id=data.get('empresa_id') or None
    )
    db.session.add(pref)
    db.session.commit()
    return jsonify(pref.to_dict())


@app.route('/api/prefeitura/<int:pref_id>', methods=['PUT', 'DELETE'])
def editar_prefeitura(pref_id):
    pref = Prefeitura.query.get_or_404(pref_id)
    if request.method == 'DELETE':
        pref.ativa = False
        db.session.commit()
        return jsonify({'ok': True})
    data = request.json
    pref.nome = data.get('nome', pref.nome)
    pref.email = data.get('email', pref.email)
    pref.contato = data.get('contato', pref.contato)
    pref.telefone = data.get('telefone', pref.telefone)
    pref.endereco = data.get('endereco', pref.endereco)
    pref.numero_contrato = data.get('numero_contrato', pref.numero_contrato)
    pref.ativa = data.get('ativa', pref.ativa)
    pref.empresa_id = data.get('empresa_id') or None
    db.session.commit()
    return jsonify(pref.to_dict())


# ============================================================
# FORNECEDORES
# ============================================================

@app.route('/fornecedores')
def fornecedores():
    lista = Fornecedor.query.order_by(Fornecedor.nome).all()
    return render_template('fornecedores.html', fornecedores=lista, config=get_config())


@app.route('/api/fornecedor', methods=['POST'])
def criar_fornecedor():
    data = request.json
    forn = Fornecedor(
        nome=data['nome'],
        telefone_whatsapp=data.get('telefone_whatsapp', ''),
        tipo=data.get('tipo', 'local'),
        categorias=data.get('categorias', '')
    )
    db.session.add(forn)
    db.session.commit()
    return jsonify(forn.to_dict())


@app.route('/api/fornecedor/<int:forn_id>', methods=['PUT', 'DELETE'])
def editar_fornecedor(forn_id):
    forn = Fornecedor.query.get_or_404(forn_id)
    if request.method == 'DELETE':
        forn.ativo = False
        db.session.commit()
        return jsonify({'ok': True})
    data = request.json
    forn.nome = data.get('nome', forn.nome)
    forn.telefone_whatsapp = data.get('telefone_whatsapp', forn.telefone_whatsapp)
    forn.tipo = data.get('tipo', forn.tipo)
    forn.categorias = data.get('categorias', forn.categorias)
    forn.ativo = data.get('ativo', forn.ativo)
    db.session.commit()
    return jsonify(forn.to_dict())


# ============================================================
# EMPRESAS
# ============================================================

@app.route('/empresas')
def empresas():
    lista = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()
    return render_template('empresas.html', empresas=lista, config=get_config())


@app.route('/api/empresa', methods=['POST'])
def criar_empresa():
    data = request.json
    emp = Empresa(
        nome=data['nome'],
        cnpj=data.get('cnpj', ''),
        endereco=data.get('endereco', ''),
        telefone=data.get('telefone', '')
    )
    db.session.add(emp)
    db.session.commit()
    return jsonify(emp.to_dict())


@app.route('/api/empresa/<int:emp_id>', methods=['PUT', 'DELETE'])
def editar_empresa(emp_id):
    emp = Empresa.query.get_or_404(emp_id)
    if request.method == 'DELETE':
        emp.ativa = False
        db.session.commit()
        return jsonify({'ok': True})
    data = request.json
    emp.nome = data.get('nome', emp.nome)
    emp.cnpj = data.get('cnpj', emp.cnpj)
    emp.endereco = data.get('endereco', emp.endereco)
    emp.telefone = data.get('telefone', emp.telefone)
    db.session.commit()
    return jsonify(emp.to_dict())


@app.route('/api/lista/<int:lista_id>/whatsapp/fornecedor/<int:fornecedor_id>', methods=['GET'])
def gerar_whatsapp_fornecedor(lista_id, fornecedor_id):
    lista = ListaConsolidada.query.get_or_404(lista_id)
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    cfg = get_config()

    itens_dict = [i.to_dict() for i in lista.itens]
    categorias = fornecedor.get_categorias()
    mensagem = gerar_mensagem_fornecedor(itens_dict, fornecedor.nome, categorias, cfg.nome_empresa)

    # Formata número para wa.me (apenas dígitos, inclui 55 do Brasil)
    tel = (fornecedor.telefone_whatsapp or '').strip()
    tel_digits = ''.join(c for c in tel if c.isdigit())
    if tel_digits and not tel_digits.startswith('55'):
        tel_digits = '55' + tel_digits

    return jsonify({
        'mensagem': mensagem,
        'fornecedor': fornecedor.nome,
        'telefone': tel_digits
    })


# ============================================================
# CONFIGURAÇÕES
# ============================================================

@app.route('/configuracoes')
def configuracoes():
    cfg = get_config()
    email_cfgs = get_email_configs()
    return render_template('configuracoes.html', cfg=cfg, email_cfgs=email_cfgs, config=cfg)


@app.route('/api/configuracoes/geral', methods=['POST'])
def salvar_config_geral():
    data = request.json
    cfg = get_config()
    cfg.nome_empresa = data.get('nome_empresa', cfg.nome_empresa)
    cfg.cidade = data.get('cidade', cfg.cidade)
    cfg.telefone = data.get('telefone', cfg.telefone)
    if data.get('anthropic_api_key'):
        cfg.anthropic_api_key = data['anthropic_api_key']
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/configuracoes/email', methods=['POST'])
def adicionar_email():
    """Adiciona nova conta de email (suporta múltiplas contas)."""
    data = request.json
    email_addr = data.get('email_address', '').strip()
    if not email_addr:
        return jsonify({'erro': 'Email não informado.'}), 400

    # Auto-detecta servidor IMAP
    servidor = data.get('imap_server', '')
    porta = int(data.get('imap_port', 993))
    if not servidor and '@' in email_addr:
        servidor, porta = detectar_servidor(email_addr)

    # Verifica se já existe
    existente = ConfiguracaoEmail.query.filter(
        db.func.lower(ConfiguracaoEmail.email_address) == email_addr.lower()
    ).first()

    if existente:
        existente.email_password = data.get('email_password', existente.email_password)
        existente.imap_server = servidor
        existente.imap_port = porta
        existente.pasta = data.get('pasta', existente.pasta)
        existente.nome = data.get('nome', existente.nome)
        existente.ativa = True
    else:
        novo = ConfiguracaoEmail(
            nome=data.get('nome', email_addr),
            email_address=email_addr,
            email_password=data.get('email_password', ''),
            imap_server=servidor,
            imap_port=porta,
            pasta=data.get('pasta', 'INBOX'),
            ativa=True
        )
        db.session.add(novo)

    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/configuracoes/email/<int:email_id>', methods=['DELETE'])
def remover_email(email_id):
    ec = ConfiguracaoEmail.query.get_or_404(email_id)
    db.session.delete(ec)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/configuracoes/email/testar', methods=['POST'])
def testar_email():
    data = request.json
    email_addr = data.get('email_address', '')
    password = data.get('email_password', '')
    servidor = data.get('imap_server', '')
    porta = int(data.get('imap_port', 993))
    if not servidor and '@' in email_addr:
        servidor, porta = detectar_servidor(email_addr)
    ok, mensagem = testar_conexao(email_addr, password, servidor, porta)
    return jsonify({'ok': ok, 'mensagem': mensagem})


@app.route('/api/configuracoes/email/testar-id/<int:email_id>', methods=['POST'])
def testar_email_id(email_id):
    ec = ConfiguracaoEmail.query.get_or_404(email_id)
    ok, mensagem = testar_conexao(ec.email_address, ec.email_password, ec.imap_server, ec.imap_port)
    return jsonify({'ok': ok, 'mensagem': mensagem})


@app.route('/api/pedidos', methods=['GET'])
def listar_pedidos():
    status = request.args.get('status')
    q = Pedido.query
    if status:
        q = q.filter_by(status=status)
    pedidos = q.order_by(Pedido.criado_em.desc()).limit(50).all()
    return jsonify([p.to_dict() for p in pedidos])


@app.route('/api/pedido/<int:pedido_id>/entregar', methods=['POST'])
def marcar_entregue(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    pedido.status = 'entregue'
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/pedido/<int:pedido_id>/status', methods=['POST'])
def atualizar_status_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    novo_status = request.json.get('status')
    if novo_status in ['novo', 'processado', 'comprado', 'entregue']:
        pedido.status = novo_status
        db.session.commit()
    return jsonify({'ok': True, 'status': pedido.status})


@app.route('/api/pedido/<int:pedido_id>/deletar', methods=['POST'])
def deletar_pedido(pedido_id):
    """Deleta um pedido e seus itens para permitir re-processamento."""
    pedido = Pedido.query.get_or_404(pedido_id)
    ItemPedido.query.filter_by(pedido_id=pedido_id).delete()
    db.session.delete(pedido)
    db.session.commit()
    return jsonify({'ok': True, 'mensagem': 'Pedido deletado. Agora rode o Apps Script novamente para re-processar.'})


@app.route('/api/pedido/<int:pedido_id>/reprocessar', methods=['POST'])
def reprocessar_pedido(pedido_id):
    """Re-processa um pedido com a IA usando o conteúdo já armazenado."""
    pedido = Pedido.query.get_or_404(pedido_id)
    cfg = get_config()
    api_key = cfg.anthropic_api_key
    if not api_key:
        return jsonify({'erro': 'Chave da API não configurada. Vá em Configurações.'}), 400

    # Usa o conteúdo já extraído e armazenado
    conteudo = pedido.corpo_email or ''
    if not conteudo.strip():
        return jsonify({'erro': 'Email sem conteúdo armazenado. Delete e reenvie pelo Apps Script.'}), 400

    # Monta um email_data sintético com o conteúdo já extraído
    email_data_fake = {
        'assunto': pedido.assunto_email or '',
        'email_remetente': pedido.email_remetente or '',
        'data_recebimento': '',
        'texto_corpo': conteudo,
        'anexos': []
    }

    resultado_ia = extrair_pedido_com_ia(email_data_fake, api_key)
    if resultado_ia.get('erro'):
        return jsonify({'erro': resultado_ia['erro']}), 500

    # Remove itens antigos e adiciona novos
    ItemPedido.query.filter_by(pedido_id=pedido_id).delete()

    for item_data in resultado_ia.get('itens', []):
        produto_nome = item_data.get('produto', '').strip()
        if not produto_nome:
            continue
        destino = sugerir_destino_produto(produto_nome, api_key)
        db.session.add(ItemPedido(
            pedido_id=pedido.id,
            produto=produto_nome,
            quantidade=float(item_data.get('quantidade', 0)),
            unidade=item_data.get('unidade', 'kg'),
            observacao=item_data.get('observacao', ''),
            destino=destino
        ))

    # Atualiza dados do pedido
    if resultado_ia.get('data_entrega'):
        pedido.data_entrega = resultado_ia['data_entrega']
    if resultado_ia.get('observacoes'):
        pedido.observacoes = resultado_ia['observacoes']

    db.session.commit()
    total_itens = len(resultado_ia.get('itens', []))
    return jsonify({'ok': True, 'mensagem': f'Re-processado! {total_itens} itens extraídos.', 'total_itens': total_itens})


@app.route('/api/setup/inicializar', methods=['POST'])
def setup_inicializar():
    """Endpoint para forçar re-inicialização dos dados padrão."""
    token = request.json.get('token', '') if request.json else ''
    if token != 'monica2024':
        return jsonify({'erro': 'Token inválido'}), 401
    with app.app_context():
        _inicializar_dados()
    return jsonify({'ok': True, 'mensagem': 'Dados inicializados com sucesso!'})


@app.route('/api/fluxo-semana', methods=['GET'])
def fluxo_semana():
    """Retorna o status do fluxo da semana para o painel visual."""
    pedidos_novos = Pedido.query.filter_by(status='novo').count()
    pedidos_processados = Pedido.query.filter_by(status='processado').count()
    pedidos_comprado = Pedido.query.filter_by(status='comprado').count()
    pedidos_entregue = Pedido.query.filter_by(status='entregue').count()
    total_pedidos = pedidos_novos + pedidos_processados + pedidos_comprado + pedidos_entregue

    lista = ListaConsolidada.query.filter(
        ListaConsolidada.status.in_(['aberta', 'cotacao'])
    ).order_by(ListaConsolidada.data_criacao.desc()).first()

    fornecedores = Fornecedor.query.filter(
        Fornecedor.ativo == True,
        Fornecedor.categorias != '',
        Fornecedor.telefone_whatsapp != ''
    ).all()

    vales_gerados = ValeEntrega.query.count()

    return jsonify({
        'pedidos_novos': pedidos_novos,
        'pedidos_processados': pedidos_processados,
        'pedidos_comprado': pedidos_comprado,
        'pedidos_entregue': pedidos_entregue,
        'total_pedidos': total_pedidos,
        'lista_id': lista.id if lista else None,
        'lista_status': lista.status if lista else None,
        'lista_itens': len(lista.itens) if lista else 0,
        'fornecedores': [{'id': f.id, 'nome': f.nome} for f in fornecedores],
        'vales_gerados': vales_gerados,
    })


@app.route('/api/buscar-emails-automatico', methods=['POST'])
def buscar_emails_automatico():
    """Endpoint chamado pela busca automática de sexta-feira."""
    return buscar_emails()


# ============================================================
# AGENDADOR — BUSCA AUTOMÁTICA DIÁRIA
# ============================================================
_ultima_busca_automatica = None

def _agendador_diario():
    """
    Thread que busca emails automaticamente:
    - Todos os dias úteis (seg-sex) às 08:00, 12:00 e 16:00
    - Na sexta, também às 15:30 (para garantir que tudo esteja pronto antes das 16:30)
    """
    global _ultima_busca_automatica
    with app.app_context():
        while True:
            agora = datetime.now()
            dia_semana = agora.weekday()  # 0=seg, 4=sex, 5=sab, 6=dom
            hora = agora.hour
            minuto = agora.minute
            chave = agora.strftime('%Y-%m-%d-%H')  # única por hora

            # Dias úteis apenas (seg=0 a sex=4)
            e_dia_util = dia_semana <= 4
            # Horários de busca: 08:00, 12:00, 16:00 (e 15:30 na sexta)
            e_horario_busca = hora in (8, 12, 16) or (dia_semana == 4 and hora == 15 and minuto >= 30)

            if e_dia_util and e_horario_busca and _ultima_busca_automatica != chave:
                print(f'\n[AGENDADOR] Busca automática iniciada — {agora.strftime("%d/%m/%Y %H:%M")}')
                try:
                    with app.test_request_context():
                        buscar_emails()
                    _ultima_busca_automatica = chave
                    print(f'[AGENDADOR] Busca concluída.')
                except Exception as e:
                    print(f'[AGENDADOR] Erro: {e}')

            # Verifica a cada 15 minutos
            time.sleep(900)


# Inicia agendador após definição da função (funciona no gunicorn e no flask dev)
_t_agendador = threading.Thread(target=_agendador_diario, daemon=True)
_t_agendador.start()

if __name__ == '__main__':
    print('  Agendador ativo: busca automatica diaria (08:00, 12:00, 16:00 dias uteis)')

    print('\n' + '='*55)
    print('  Sistema de Pedidos - Profeta e Fortumel')
    print('='*55)
    print('  Acesse no navegador: http://localhost:5000')
    print('='*55 + '\n')
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
