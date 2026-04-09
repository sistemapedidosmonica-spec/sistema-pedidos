from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from datetime import datetime
import os


def formatar_valor(valor):
    if valor is None:
        return '-'
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def formatar_qtd(qtd):
    if qtd is None:
        return '-'
    if qtd == int(qtd):
        return str(int(qtd))
    return f'{qtd:.2f}'.replace('.', ',')


def gerar_vale_entrega(pedido, itens, nome_empresa, telefone_empresa, caminho_saida, empresa=None):
    """
    Gera um vale de entrega em PDF para um pedido específico.
    """
    # Usa nome da empresa específica se disponível, senão usa nome_empresa padrão
    nome_cabecalho = empresa.nome if empresa else nome_empresa
    telefone_cabecalho = empresa.telefone if (empresa and empresa.telefone) else telefone_empresa
    doc = SimpleDocTemplate(
        caminho_saida,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm
    )

    estilos = getSampleStyleSheet()

    # Estilos personalizados
    estilo_titulo = ParagraphStyle(
        'Titulo',
        parent=estilos['Normal'],
        fontSize=18,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER,
        spaceAfter=4,
        textColor=colors.HexColor('#2E7D32')
    )

    estilo_subtitulo = ParagraphStyle(
        'Subtitulo',
        parent=estilos['Normal'],
        fontSize=11,
        fontName='Helvetica',
        alignment=TA_CENTER,
        spaceAfter=2,
        textColor=colors.HexColor('#555555')
    )

    estilo_info = ParagraphStyle(
        'Info',
        parent=estilos['Normal'],
        fontSize=10,
        fontName='Helvetica',
        alignment=TA_LEFT,
        spaceAfter=2
    )

    estilo_info_bold = ParagraphStyle(
        'InfoBold',
        parent=estilos['Normal'],
        fontSize=10,
        fontName='Helvetica-Bold',
        alignment=TA_LEFT,
        spaceAfter=2
    )

    estilo_rodape = ParagraphStyle(
        'Rodape',
        parent=estilos['Normal'],
        fontSize=8,
        fontName='Helvetica',
        alignment=TA_CENTER,
        textColor=colors.grey
    )

    elementos = []

    # ---- CABEÇALHO ----
    elementos.append(Paragraph(nome_cabecalho.upper(), estilo_titulo))
    elementos.append(Paragraph(f'Tel: {telefone_cabecalho}', estilo_subtitulo))
    elementos.append(Spacer(1, 0.3 * cm))
    elementos.append(HRFlowable(width='100%', thickness=2, color=colors.HexColor('#2E7D32')))
    elementos.append(Spacer(1, 0.3 * cm))

    # Título do documento
    titulo_doc = ParagraphStyle(
        'TituloDoc',
        parent=estilos['Normal'],
        fontSize=14,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER,
        spaceAfter=6,
        textColor=colors.HexColor('#1A1A1A')
    )
    elementos.append(Paragraph('VALE DE ENTREGA', titulo_doc))
    elementos.append(Spacer(1, 0.3 * cm))

    # ---- INFORMAÇÕES DO PEDIDO ----
    prefeitura_nome = pedido.prefeitura.nome if pedido.prefeitura else 'N/A'
    prefeitura_endereco = pedido.prefeitura.endereco if pedido.prefeitura else ''
    data_emissao = datetime.now().strftime('%d/%m/%Y')
    data_entrega = pedido.data_entrega or 'A combinar'
    numero_vale = pedido.vale.numero_vale if pedido.vale else f'V{pedido.id:04d}'

    info_data = [
        ['Nº do Vale:', numero_vale, 'Data de Emissão:', data_emissao],
        ['Destino:', prefeitura_nome, 'Data de Entrega:', data_entrega],
        ['Endereço:', prefeitura_endereco, '', ''],
    ]

    tabela_info = Table(info_data, colWidths=[3.5 * cm, 7 * cm, 3.5 * cm, 4.5 * cm])
    tabela_info.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FBE7')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#DDDDDD')),
    ]))
    elementos.append(tabela_info)
    elementos.append(Spacer(1, 0.5 * cm))

    # ---- TABELA DE ITENS ----
    cabecalho = [
        Paragraph('<b>Nº</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER)),
        Paragraph('<b>Produto</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_LEFT)),
        Paragraph('<b>Qtd</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER)),
        Paragraph('<b>Un.</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER)),
        Paragraph('<b>Preço Unit.</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
        Paragraph('<b>Total</b>', ParagraphStyle('ch', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
    ]

    dados_tabela = [cabecalho]
    total_geral = 0

    for i, item in enumerate(itens, 1):
        produto = item.produto
        qtd = item.quantidade
        unidade = item.unidade or 'kg'
        preco = None
        total_item = None

        # Busca preço na lista consolidada se disponível
        if hasattr(item, 'preco_unit') and item.preco_unit:
            preco = item.preco_unit
            total_item = qtd * preco
            total_geral += total_item

        linha = [
            Paragraph(str(i), ParagraphStyle('c', fontSize=9, alignment=TA_CENTER)),
            Paragraph(produto, ParagraphStyle('c', fontSize=9, alignment=TA_LEFT)),
            Paragraph(formatar_qtd(qtd), ParagraphStyle('c', fontSize=9, alignment=TA_CENTER)),
            Paragraph(unidade, ParagraphStyle('c', fontSize=9, alignment=TA_CENTER)),
            Paragraph(formatar_valor(preco), ParagraphStyle('c', fontSize=9, alignment=TA_RIGHT)),
            Paragraph(formatar_valor(total_item), ParagraphStyle('c', fontSize=9, alignment=TA_RIGHT)),
        ]
        dados_tabela.append(linha)

    # Linha de total
    linha_total = [
        '', '', '', '',
        Paragraph('<b>TOTAL:</b>', ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
        Paragraph(f'<b>{formatar_valor(total_geral if total_geral > 0 else None)}</b>',
                  ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
    ]
    dados_tabela.append(linha_total)

    tabela_itens = Table(
        dados_tabela,
        colWidths=[1 * cm, 9 * cm, 2 * cm, 1.8 * cm, 3 * cm, 3 * cm]
    )
    tabela_itens.setStyle(TableStyle([
        # Cabeçalho
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2E7D32')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),

        # Linhas alternadas
        ('BACKGROUND', (0, 1), (-1, -2), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F5F5F5')]),

        # Linha de total
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E8F5E9')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),

        # Bordas
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#AAAAAA')),
        ('INNERGRID', (0, 0), (-1, -2), 0.25, colors.HexColor('#DDDDDD')),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#2E7D32')),

        # Padding
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elementos.append(tabela_itens)

    # ---- OBSERVAÇÕES ----
    if pedido.observacoes:
        elementos.append(Spacer(1, 0.5 * cm))
        elementos.append(Paragraph(f'<b>Observações:</b> {pedido.observacoes}', estilo_info))

    # ---- ASSINATURAS ----
    elementos.append(Spacer(1, 1.5 * cm))
    dados_assinatura = [
        ['_' * 40, '', '_' * 40],
        ['Responsável pela entrega', '', 'Recebido por / Carimbo'],
        ['', '', ''],
        [nome_cabecalho, '', prefeitura_nome],
    ]
    tabela_assinatura = Table(dados_assinatura, colWidths=[8 * cm, 2 * cm, 8 * cm])
    tabela_assinatura.setStyle(TableStyle([
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 3), (-1, 3), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 3), (-1, 3), 9),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'LEFT'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elementos.append(tabela_assinatura)

    # ---- RODAPÉ ----
    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(HRFlowable(width='100%', thickness=0.5, color=colors.grey))
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(Paragraph(
        f'Documento gerado em {datetime.now().strftime("%d/%m/%Y às %H:%M")} — Sistema de Pedidos {nome_cabecalho}',
        estilo_rodape
    ))

    doc.build(elementos)
    return caminho_saida


def gerar_lista_compras(itens_ceasa, itens_local, nome_empresa, caminho_saida):
    """Gera PDF com as listas de compras (CEASA e local)."""
    doc = SimpleDocTemplate(
        caminho_saida,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm
    )

    estilos = getSampleStyleSheet()
    elementos = []
    hoje = datetime.now().strftime('%d/%m/%Y')

    def cabecalho_secao(titulo, cor):
        return Paragraph(
            titulo,
            ParagraphStyle('sec', fontName='Helvetica-Bold', fontSize=13,
                           textColor=colors.white, backColor=cor,
                           alignment=TA_CENTER, spaceAfter=4, spaceBefore=8,
                           leftPadding=5, rightPadding=5, topPadding=4, bottomPadding=4)
        )

    def tabela_itens_compras(itens):
        cab = [
            Paragraph('<b>Produto</b>', ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=10)),
            Paragraph('<b>Quantidade</b>', ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER)),
            Paragraph('<b>Melhor Preço</b>', ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
            Paragraph('<b>Fornecedor</b>', ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=10)),
            Paragraph('<b>Total</b>', ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
        ]
        dados = [cab]
        total = 0
        for item in itens:
            qtd = item.get('quantidade_total', 0)
            preco = item.get('melhor_preco')
            total_item = (qtd * preco) if preco else None
            if total_item:
                total += total_item
            dados.append([
                Paragraph(item.get('produto', ''), ParagraphStyle('c', fontSize=9)),
                Paragraph(f"{formatar_qtd(qtd)} {item.get('unidade', 'kg')}", ParagraphStyle('c', fontSize=9, alignment=TA_CENTER)),
                Paragraph(formatar_valor(preco), ParagraphStyle('c', fontSize=9, alignment=TA_RIGHT)),
                Paragraph(item.get('melhor_fornecedor', '-'), ParagraphStyle('c', fontSize=9)),
                Paragraph(formatar_valor(total_item), ParagraphStyle('c', fontSize=9, alignment=TA_RIGHT)),
            ])
        dados.append([
            '', '',
            Paragraph('<b>TOTAL:</b>', ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
            '',
            Paragraph(f'<b>{formatar_valor(total if total > 0 else None)}</b>',
                      ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
        ])
        t = Table(dados, colWidths=[6 * cm, 3 * cm, 3 * cm, 4 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#37474F')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#ECEFF1')]),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ECEFF1')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
            ('INNERGRID', (0, 0), (-1, -2), 0.25, colors.HexColor('#DDDDDD')),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#37474F')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return t

    # Título
    elementos.append(Paragraph(
        f'{nome_empresa} — Lista de Compras',
        ParagraphStyle('T', fontName='Helvetica-Bold', fontSize=16, alignment=TA_CENTER,
                       textColor=colors.HexColor('#1A1A1A'), spaceAfter=4)
    ))
    elementos.append(Paragraph(
        f'Gerado em: {hoje}',
        ParagraphStyle('D', fontName='Helvetica', fontSize=10, alignment=TA_CENTER,
                       textColor=colors.grey, spaceAfter=10)
    ))
    elementos.append(HRFlowable(width='100%', thickness=1.5, color=colors.HexColor('#37474F')))
    elementos.append(Spacer(1, 0.4 * cm))

    # Seção CEASA
    if itens_ceasa:
        elementos.append(cabecalho_secao(f'CEASA — {len(itens_ceasa)} produto(s)', colors.HexColor('#1B5E20')))
        elementos.append(tabela_itens_compras(itens_ceasa))
        elementos.append(Spacer(1, 0.5 * cm))

    # Seção Local
    if itens_local:
        elementos.append(cabecalho_secao(f'COMPRAS LOCAIS — {len(itens_local)} produto(s)', colors.HexColor('#E65100')))
        elementos.append(tabela_itens_compras(itens_local))

    doc.build(elementos)
    return caminho_saida
