from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Prefeitura(db.Model):
    __tablename__ = 'prefeituras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    contato = db.Column(db.String(100))
    telefone = db.Column(db.String(30))
    endereco = db.Column(db.String(300))
    numero_contrato = db.Column(db.String(100))
    ativa = db.Column(db.Boolean, default=True)
    criada_em = db.Column(db.DateTime, default=datetime.now)
    # Vínculo fixo com a empresa responsável pelo contrato
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)

    pedidos = db.relationship('Pedido', backref='prefeitura', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'email': self.email,
            'contato': self.contato,
            'telefone': self.telefone,
            'endereco': self.endereco,
            'numero_contrato': self.numero_contrato,
            'ativa': self.ativa,
            'empresa_id': self.empresa_id,
            'empresa_nome': self.empresa.nome if self.empresa else ''
        }


class Empresa(db.Model):
    __tablename__ = 'empresas'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(30), default='')
    endereco = db.Column(db.String(300), default='')
    telefone = db.Column(db.String(30), default='')
    ativa = db.Column(db.Boolean, default=True)

    pedidos = db.relationship('Pedido', backref='empresa', lazy=True)
    prefeituras = db.relationship('Prefeitura', backref='empresa', lazy=True, foreign_keys='Prefeitura.empresa_id')

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'cnpj': self.cnpj,
            'endereco': self.endereco,
            'telefone': self.telefone,
            'ativa': self.ativa,
            'total_prefeituras': len(self.prefeituras)
        }


class Pedido(db.Model):
    __tablename__ = 'pedidos'
    id = db.Column(db.Integer, primary_key=True)
    prefeitura_id = db.Column(db.Integer, db.ForeignKey('prefeituras.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    assunto_email = db.Column(db.String(500))
    email_id = db.Column(db.String(200))
    data_recebimento = db.Column(db.DateTime)
    data_entrega = db.Column(db.String(50))
    status = db.Column(db.String(30), default='novo')  # novo, processado, comprado, entregue
    observacoes = db.Column(db.Text)
    email_remetente = db.Column(db.String(200))
    corpo_email = db.Column(db.Text)   # conteúdo original do email para conferência
    criado_em = db.Column(db.DateTime, default=datetime.now)

    itens = db.relationship('ItemPedido', backref='pedido', lazy=True, cascade='all, delete-orphan')
    vale = db.relationship('ValeEntrega', backref='pedido', uselist=False)

    def to_dict(self):
        return {
            'id': self.id,
            'prefeitura_id': self.prefeitura_id,
            'prefeitura_nome': self.prefeitura.nome if self.prefeitura else '',
            'empresa_nome': self.empresa.nome if self.empresa else '',
            'assunto_email': self.assunto_email,
            'data_recebimento': self.data_recebimento.strftime('%d/%m/%Y %H:%M') if self.data_recebimento else '',
            'data_entrega': self.data_entrega,
            'status': self.status,
            'observacoes': self.observacoes,
            'total_itens': len(self.itens)
        }


class ItemPedido(db.Model):
    __tablename__ = 'itens_pedido'
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos.id'), nullable=False)
    produto = db.Column(db.String(200), nullable=False)
    quantidade = db.Column(db.Float, nullable=False)
    unidade = db.Column(db.String(30), default='kg')
    observacao = db.Column(db.String(300))
    destino = db.Column(db.String(20), default='local')  # 'ceasa' ou 'local'

    def to_dict(self):
        return {
            'id': self.id,
            'pedido_id': self.pedido_id,
            'produto': self.produto,
            'quantidade': self.quantidade,
            'unidade': self.unidade,
            'observacao': self.observacao,
            'destino': self.destino
        }


class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False, unique=True)
    categoria = db.Column(db.String(100))
    destino_padrao = db.Column(db.String(20), default='local')  # 'ceasa' ou 'local'
    unidade_padrao = db.Column(db.String(30), default='kg')

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'categoria': self.categoria,
            'destino_padrao': self.destino_padrao,
            'unidade_padrao': self.unidade_padrao
        }


class Fornecedor(db.Model):
    __tablename__ = 'fornecedores'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    telefone_whatsapp = db.Column(db.String(30))
    tipo = db.Column(db.String(20), default='local')  # 'ceasa', 'local', 'ambos'
    # Categorias que este fornecedor atende (separadas por vírgula)
    # ex: "genero,carne,limpeza,hortifruti,uniforme"
    categorias = db.Column(db.String(200), default='')
    ativo = db.Column(db.Boolean, default=True)

    cotacoes = db.relationship('Cotacao', backref='fornecedor', lazy=True)

    def get_categorias(self):
        return [c.strip() for c in (self.categorias or '').split(',') if c.strip()]

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'telefone_whatsapp': self.telefone_whatsapp,
            'tipo': self.tipo,
            'categorias': self.categorias or '',
            'ativo': self.ativo
        }


class ListaConsolidada(db.Model):
    __tablename__ = 'lista_consolidada'
    id = db.Column(db.Integer, primary_key=True)
    semana_ref = db.Column(db.String(30))  # ex: "2024-W45"
    data_criacao = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(30), default='aberta')  # aberta, cotacao, finalizada, comprada
    total_estimado = db.Column(db.Float, default=0)

    itens = db.relationship('ItemConsolidado', backref='lista', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'semana_ref': self.semana_ref,
            'data_criacao': self.data_criacao.strftime('%d/%m/%Y %H:%M'),
            'status': self.status,
            'total_estimado': self.total_estimado,
            'total_itens': len(self.itens)
        }


class ItemConsolidado(db.Model):
    __tablename__ = 'itens_consolidados'
    id = db.Column(db.Integer, primary_key=True)
    lista_id = db.Column(db.Integer, db.ForeignKey('lista_consolidada.id'), nullable=False)
    produto = db.Column(db.String(200), nullable=False)
    quantidade_total = db.Column(db.Float, nullable=False)
    unidade = db.Column(db.String(30), default='kg')
    destino = db.Column(db.String(20), default='local')  # 'ceasa' ou 'local'
    melhor_preco = db.Column(db.Float)
    melhor_fornecedor = db.Column(db.String(200))
    total_valor = db.Column(db.Float)

    cotacoes = db.relationship('Cotacao', backref='item_consolidado', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'produto': self.produto,
            'quantidade_total': self.quantidade_total,
            'unidade': self.unidade,
            'destino': self.destino,
            'melhor_preco': self.melhor_preco,
            'melhor_fornecedor': self.melhor_fornecedor,
            'total_valor': self.total_valor
        }


class Cotacao(db.Model):
    __tablename__ = 'cotacoes'
    id = db.Column(db.Integer, primary_key=True)
    item_consolidado_id = db.Column(db.Integer, db.ForeignKey('itens_consolidados.id'), nullable=False)
    fornecedor_id = db.Column(db.Integer, db.ForeignKey('fornecedores.id'), nullable=False)
    preco = db.Column(db.Float, nullable=False)
    data_cotacao = db.Column(db.DateTime, default=datetime.now)
    observacao = db.Column(db.String(300))

    def to_dict(self):
        return {
            'id': self.id,
            'item_consolidado_id': self.item_consolidado_id,
            'fornecedor_id': self.fornecedor_id,
            'fornecedor_nome': self.fornecedor.nome if self.fornecedor else '',
            'preco': self.preco,
            'data_cotacao': self.data_cotacao.strftime('%d/%m/%Y %H:%M'),
            'observacao': self.observacao
        }


class ValeEntrega(db.Model):
    __tablename__ = 'vales_entrega'
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos.id'), nullable=False)
    numero_vale = db.Column(db.String(50), unique=True)
    data_emissao = db.Column(db.DateTime, default=datetime.now)
    arquivo_pdf = db.Column(db.String(500))
    valor_total = db.Column(db.Float)
    assinado = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id': self.id,
            'pedido_id': self.pedido_id,
            'numero_vale': self.numero_vale,
            'data_emissao': self.data_emissao.strftime('%d/%m/%Y'),
            'arquivo_pdf': self.arquivo_pdf,
            'valor_total': self.valor_total,
            'prefeitura_nome': self.pedido.prefeitura.nome if self.pedido and self.pedido.prefeitura else ''
        }


class ConfiguracaoEmail(db.Model):
    __tablename__ = 'configuracao_email'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), default='Email Principal')
    imap_server = db.Column(db.String(200), default='imap.gmail.com')
    imap_port = db.Column(db.Integer, default=993)
    email_address = db.Column(db.String(200))
    email_password = db.Column(db.String(500))
    pasta = db.Column(db.String(100), default='INBOX')
    ativa = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'imap_server': self.imap_server,
            'imap_port': self.imap_port,
            'email_address': self.email_address,
            'pasta': self.pasta,
            'ativa': self.ativa
        }


class ConfiguracaoGeral(db.Model):
    __tablename__ = 'configuracao_geral'
    id = db.Column(db.Integer, primary_key=True)
    nome_empresa = db.Column(db.String(200), default='Hortifruti')
    cidade = db.Column(db.String(100), default='')
    telefone = db.Column(db.String(30), default='')
    anthropic_api_key = db.Column(db.String(500), default='')
    proximo_vale = db.Column(db.Integer, default=1)

    def to_dict(self):
        return {
            'id': self.id,
            'nome_empresa': self.nome_empresa,
            'cidade': self.cidade,
            'telefone': self.telefone,
            'proximo_vale': self.proximo_vale
        }
