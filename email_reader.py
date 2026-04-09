import imaplib
import email
from email.header import decode_header
from email import policy
import re
from datetime import datetime
import io
import base64


IMAP_SERVERS = {
    'gmail.com': ('imap.gmail.com', 993),
    'googlemail.com': ('imap.gmail.com', 993),
    'outlook.com': ('imap-mail.outlook.com', 993),
    'hotmail.com': ('imap-mail.outlook.com', 993),
    'live.com': ('imap-mail.outlook.com', 993),
    'yahoo.com': ('imap.mail.yahoo.com', 993),
    'yahoo.com.br': ('imap.mail.yahoo.com', 993),
}


def detectar_servidor(email_addr):
    """Detecta automaticamente o servidor IMAP pelo domínio do email."""
    dominio = email_addr.split('@')[-1].lower()
    if dominio in IMAP_SERVERS:
        server, port = IMAP_SERVERS[dominio]
        return server, port
    return f'imap.{dominio}', 993


def decodificar_cabecalho(header_value):
    """Decodifica cabeçalhos de email que podem estar em diferentes encodings."""
    if header_value is None:
        return ''
    parts = decode_header(header_value)
    result = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(encoding or 'utf-8', errors='replace'))
            except Exception:
                result.append(part.decode('latin-1', errors='replace'))
        else:
            result.append(str(part))
    return ' '.join(result)


def conectar_email(email_address, password, imap_server, imap_port=993):
    """Conecta ao servidor IMAP e retorna a conexão."""
    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(email_address, password)
        return mail, None
    except imaplib.IMAP4.error as e:
        return None, f'Erro de autenticação: {str(e)}'
    except Exception as e:
        return None, f'Erro de conexão: {str(e)}'


def extrair_texto_email(msg):
    """Extrai o texto de um email (corpo + texto de anexos simples)."""
    texto = ''
    anexos = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get('Content-Disposition', ''))

            if 'attachment' in content_disposition:
                filename = part.get_filename()
                if filename:
                    filename = decodificar_cabecalho(filename)
                payload = part.get_payload(decode=True)
                if payload:
                    anexos.append({
                        'nome': filename or 'anexo',
                        'tipo': content_type,
                        'dados': payload,
                        'tamanho': len(payload)
                    })
            elif content_type == 'text/plain' and 'attachment' not in content_disposition:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    body = part.get_payload(decode=True).decode(charset, errors='replace')
                    texto += body + '\n'
                except Exception:
                    pass
            elif content_type == 'text/html' and not texto:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    html = part.get_payload(decode=True).decode(charset, errors='replace')
                    # Remove tags HTML de forma simples
                    texto_html = re.sub(r'<[^>]+>', ' ', html)
                    texto_html = re.sub(r'\s+', ' ', texto_html).strip()
                    texto += texto_html + '\n'
                except Exception:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or 'utf-8'
            texto = msg.get_payload(decode=True).decode(charset, errors='replace')
        except Exception:
            texto = str(msg.get_payload())

    return texto.strip(), anexos


def ler_emails_por_data(email_address, password, imap_server, imap_port=993,
                        data_inicio=None, data_fim=None, emails_conhecidos=None):
    """
    Lê emails de um intervalo de datas (independente de lido/não lido).
    data_inicio e data_fim: objetos date ou datetime.
    """
    from datetime import date as date_type, timedelta

    mail, erro = conectar_email(email_address, password, imap_server, imap_port)
    if erro:
        return [], erro

    emails_conhecidos = emails_conhecidos or set()
    emails_lidos = []

    try:
        mail.select('INBOX')

        # Monta critério de busca por data
        criterios = []
        if data_inicio:
            d = data_inicio if isinstance(data_inicio, date_type) else data_inicio.date()
            criterios.append(f'SINCE {d.strftime("%d-%b-%Y")}')
        if data_fim:
            # BEFORE é exclusivo no IMAP, então soma 1 dia
            d = data_fim if isinstance(data_fim, date_type) else data_fim.date()
            d_fim = d + timedelta(days=1)
            criterios.append(f'BEFORE {d_fim.strftime("%d-%b-%Y")}')

        busca = '(' + ' '.join(criterios) + ')' if criterios else 'ALL'
        _, mensagens = mail.search(None, busca)

        if not mensagens[0]:
            mail.logout()
            return [], None

        ids = mensagens[0].split()
        for email_id in ids:
            email_id_str = email_id.decode()
            if email_id_str in emails_conhecidos:
                continue
            try:
                _, dados = mail.fetch(email_id, '(RFC822)')
                raw_email = dados[0][1]
                msg = email.message_from_bytes(raw_email, policy=policy.default)
                assunto   = decodificar_cabecalho(msg.get('Subject', ''))
                remetente = decodificar_cabecalho(msg.get('From', ''))
                data_str  = msg.get('Date', '')
                data_recebimento = None
                try:
                    from email.utils import parsedate_to_datetime
                    data_recebimento = parsedate_to_datetime(data_str)
                except Exception:
                    data_recebimento = datetime.now()
                texto_corpo, anexos = extrair_texto_email(msg)
                match_email = re.search(r'[\w\.\+\-]+@[\w\.-]+', remetente)
                email_remetente = match_email.group(0) if match_email else remetente
                emails_lidos.append({
                    'email_id': email_id_str,
                    'assunto': assunto,
                    'remetente': remetente,
                    'email_remetente': email_remetente,
                    'data_recebimento': data_recebimento,
                    'texto_corpo': texto_corpo,
                    'anexos': anexos,
                    'raw': raw_email
                })
            except Exception as e:
                continue

        mail.logout()
        return emails_lidos, None

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return [], f'Erro: {str(e)}'


def ler_emails_novos(email_address, password, imap_server, imap_port=993, emails_conhecidos=None):
    """
    Lê emails não lidos da caixa de entrada.
    Retorna lista de emails com informações e conteúdo.
    """
    mail, erro = conectar_email(email_address, password, imap_server, imap_port)
    if erro:
        return [], erro

    emails_conhecidos = emails_conhecidos or set()
    emails_lidos = []

    try:
        mail.select('INBOX')
        # Busca emails não lidos
        _, mensagens = mail.search(None, 'UNSEEN')

        if not mensagens[0]:
            mail.logout()
            return [], None

        ids = mensagens[0].split()

        for email_id in ids[-50:]:  # Limita aos últimos 50 não lidos
            email_id_str = email_id.decode()

            if email_id_str in emails_conhecidos:
                continue

            try:
                _, dados = mail.fetch(email_id, '(RFC822)')
                raw_email = dados[0][1]
                msg = email.message_from_bytes(raw_email, policy=policy.default)

                assunto = decodificar_cabecalho(msg.get('Subject', ''))
                remetente = decodificar_cabecalho(msg.get('From', ''))
                data_str = msg.get('Date', '')

                # Parse da data
                data_recebimento = None
                try:
                    from email.utils import parsedate_to_datetime
                    data_recebimento = parsedate_to_datetime(data_str)
                except Exception:
                    data_recebimento = datetime.now()

                # Extrai texto e anexos
                texto_corpo, anexos = extrair_texto_email(msg)

                # Extrai endereço de email do remetente
                match_email = re.search(r'[\w\.-]+@[\w\.-]+', remetente)
                email_remetente = match_email.group(0) if match_email else remetente

                emails_lidos.append({
                    'email_id': email_id_str,
                    'assunto': assunto,
                    'remetente': remetente,
                    'email_remetente': email_remetente,
                    'data_recebimento': data_recebimento,
                    'texto_corpo': texto_corpo,
                    'anexos': anexos,
                    'raw': raw_email
                })
            except Exception as e:
                print(f'Erro ao processar email {email_id_str}: {e}')
                continue

        mail.logout()
        return emails_lidos, None

    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return [], f'Erro ao ler emails: {str(e)}'


def testar_conexao(email_address, password, imap_server, imap_port=993):
    """Testa a conexão com o servidor de email."""
    mail, erro = conectar_email(email_address, password, imap_server, imap_port)
    if erro:
        return False, erro
    try:
        mail.select('INBOX')
        _, total = mail.search(None, 'ALL')
        count = len(total[0].split()) if total[0] else 0
        mail.logout()
        return True, f'Conexão OK! {count} emails na caixa de entrada.'
    except Exception as e:
        return False, str(e)
