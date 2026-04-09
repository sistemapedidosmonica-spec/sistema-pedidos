"""
Script para busca automática de emails.
Executado pelo agendador do PythonAnywhere diariamente.
Configure no PythonAnywhere > Tasks para rodar às 08:00, 12:00 e 16:00.
"""
import sys
import os

sys.path.insert(0, '/home/monicapedidos/SistemaPedidos')
os.chdir('/home/monicapedidos/SistemaPedidos')

from app import app
from database import db, ConfiguracaoEmail, ConfiguracaoGeral, Prefeitura, Empresa, Pedido, ItemPedido, Produto
from email_reader import ler_emails_novos
from ai_parser import (extrair_pedido_com_ia, email_e_pedido,
                       extrair_empresa_prefeitura_do_assunto,
                       sugerir_destino_produto, _norm)
from datetime import datetime

print(f'[{datetime.now().strftime("%d/%m/%Y %H:%M")}] Iniciando busca automática de emails...')

with app.app_context():
    email_cfgs = ConfiguracaoEmail.query.filter_by(ativa=True).all()
    if not email_cfgs:
        print('Nenhuma conta de email configurada.')
        sys.exit(0)

    cfg = ConfiguracaoGeral.query.first()
    api_key = cfg.anthropic_api_key if cfg else ''

    emails_existentes = {p.email_id for p in Pedido.query.filter(Pedido.email_id.isnot(None)).all()}
    todas_prefeituras = Prefeitura.query.filter_by(ativa=True).all()
    todas_empresas = Empresa.query.filter_by(ativa=True).all()

    todos_emails = []
    for ecfg in email_cfgs:
        if not ecfg.email_address or not ecfg.email_password:
            continue
        emails_lidos, erro = ler_emails_novos(
            ecfg.email_address, ecfg.email_password,
            ecfg.imap_server, ecfg.imap_port, emails_existentes
        )
        if erro:
            print(f'Erro ao ler {ecfg.email_address}: {erro}')
        else:
            todos_emails.extend(emails_lidos)
            print(f'{len(emails_lidos)} emails lidos de {ecfg.email_address}')

    if not todos_emails:
        print('Nenhum email novo.')
        sys.exit(0)

    _STOPWORDS = {'prefeitura', 'municipal', 'secretaria', 'municipio', 'municipais'}

    def _encontrar_prefeitura(nome_ia):
        if not nome_ia:
            return None
        nome_norm = _norm(nome_ia)
        melhor, melhor_score = None, 0
        for p in todas_prefeituras:
            kws = [_norm(w) for w in p.nome.split() if len(w) >= 3 and _norm(w) not in _STOPWORDS]
            score = sum(1 for kw in kws if kw in nome_norm)
            if score > melhor_score:
                melhor_score, melhor = score, p
        return melhor if melhor_score >= 1 else None

    def _encontrar_empresa(nome_ia):
        if not nome_ia:
            return None
        nome_norm = _norm(nome_ia)
        for e in todas_empresas:
            palavras = [_norm(w) for w in e.nome.split() if len(w) >= 3]
            if any(p in nome_norm for p in palavras):
                return e
        return None

    novos = 0
    for email_data in todos_emails:
        assunto = email_data.get('assunto', '')
        email_rem = email_data.get('email_remetente', '').lower()

        prefeitura, empresa = extrair_empresa_prefeitura_do_assunto(
            assunto, todas_prefeituras, todas_empresas)

        if not prefeitura and not empresa:
            e_pedido, motivo = email_e_pedido(email_data)
            if not e_pedido and ' - ' not in assunto:
                print(f'Ignorado: {assunto} ({motivo})')
                continue

        resultado_ia = {}
        if api_key:
            resultado_ia = extrair_pedido_com_ia(email_data, api_key)

        if not prefeitura:
            prefeitura = _encontrar_prefeitura(resultado_ia.get('prefeitura_nome', ''))
        if not empresa:
            empresa = _encontrar_empresa(resultado_ia.get('empresa_nome', ''))
        if prefeitura and not empresa and prefeitura.empresa_id:
            empresa = Empresa.query.get(prefeitura.empresa_id)

        if not prefeitura:
            nome_pref = resultado_ia.get('prefeitura_nome') or assunto or f'Novo: {email_rem}'
            prefeitura = Prefeitura(
                nome=nome_pref, email=email_rem,
                endereco=resultado_ia.get('prefeitura_endereco', '') or '',
                contato=resultado_ia.get('prefeitura_contato', '') or '',
                empresa_id=empresa.id if empresa else None
            )
            db.session.add(prefeitura)
            db.session.flush()
            todas_prefeituras.append(prefeitura)

        if empresa and not prefeitura.empresa_id:
            prefeitura.empresa_id = empresa.id

        corpo = email_data.get('texto_corpo', '') or ''
        anexos_nomes = [a.get('nome', '') for a in email_data.get('anexos', []) if a.get('nome')]
        if anexos_nomes:
            corpo += '\n\n[Anexos: ' + ', '.join(anexos_nomes) + ']'

        pedido = Pedido(
            prefeitura_id=prefeitura.id,
            empresa_id=empresa.id if empresa else None,
            assunto_email=assunto,
            email_id=email_data.get('email_id'),
            data_recebimento=email_data.get('data_recebimento'),
            email_remetente=email_rem,
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
            db.session.add(ItemPedido(
                pedido_id=pedido.id,
                produto=produto_nome,
                quantidade=float(item_data.get('quantidade', 0)),
                unidade=item_data.get('unidade', 'kg'),
                observacao=item_data.get('observacao', ''),
                destino=destino
            ))
            if not Produto.query.filter(db.func.lower(Produto.nome) == produto_nome.lower()).first():
                db.session.add(Produto(nome=produto_nome, destino_padrao=destino))

        novos += 1
        print(f'Pedido importado: {prefeitura.nome} — {len(resultado_ia.get("itens", []))} itens')

    db.session.commit()
    print(f'Concluído: {novos} novo(s) pedido(s) importado(s).')
