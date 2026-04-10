/**
 * Sistema de Pedidos — Profeta e Fortumel
 * Lê TODOS os emails do Gmail e envia para o sistema.
 *
 * O pedido pode chegar em QUALQUER formato:
 * - PDF anexado
 * - Word (.doc, .docx) anexado
 * - Excel (.xls, .xlsx) anexado
 * - Texto diretamente no corpo do email
 * - HTML no corpo do email
 * - Qualquer combinação dos acima
 *
 * O script captura TUDO e envia para o sistema processar.
 */

var SISTEMA_URL = 'https://monicapedidos.pythonanywhere.com/api/webhook/email';
var TOKEN_SEGURANCA = 'monica2024';

function buscarEmailsNovos() {
  var props = PropertiesService.getScriptProperties();
  var ultimaData = props.getProperty('ultima_verificacao');
  var dataInicio;
  if (!ultimaData) {
    dataInicio = new Date();
    dataInicio.setDate(dataInicio.getDate() - 7);
  } else {
    dataInicio = new Date(ultimaData);
  }

  var query = 'after:' + formatarData(dataInicio);
  var threads = GmailApp.search(query, 0, 50);

  if (threads.length === 0) {
    Logger.log('Nenhum email encontrado.');
    props.setProperty('ultima_verificacao', new Date().toISOString());
    return;
  }

  var emails = [];

  for (var i = 0; i < threads.length; i++) {
    var messages = threads[i].getMessages();
    for (var j = 0; j < messages.length; j++) {
      var msg = messages[j];
      if (msg.getDate() < dataInicio) continue;

      // Captura TODAS as formas de texto do email
      var textoPlano = msg.getPlainBody() || '';
      var textoHtml  = msg.getBody() || '';

      // Usa texto plano se disponível, senão usa HTML
      var textoCorpo = textoPlano || textoHtml;
      if (textoCorpo.length > 15000) {
        textoCorpo = textoCorpo.substring(0, 15000);
      }

      var email = {
        email_id: msg.getId(),
        assunto: msg.getSubject() || '',
        email_remetente: msg.getFrom() || '',
        texto_corpo: textoCorpo,
        data_recebimento: msg.getDate().toISOString(),
        anexos: []
      };

      // Captura TODOS os anexos (qualquer formato)
      var attachments = msg.getAttachments();
      for (var k = 0; k < attachments.length; k++) {
        var att = attachments[k];
        var nomeAnexo = att.getName() || '';
        var nomeLower = nomeAnexo.toLowerCase();
        var tipo = att.getContentType() || '';

        // Aceita: PDF, Word, Excel, CSV, TXT e qualquer arquivo de texto/documento
        var ehDocumento = (
          nomeLower.endsWith('.pdf') ||
          nomeLower.endsWith('.doc') ||
          nomeLower.endsWith('.docx') ||
          nomeLower.endsWith('.xls') ||
          nomeLower.endsWith('.xlsx') ||
          nomeLower.endsWith('.csv') ||
          nomeLower.endsWith('.txt') ||
          nomeLower.endsWith('.odt') ||
          nomeLower.endsWith('.ods') ||
          tipo.indexOf('pdf') >= 0 ||
          tipo.indexOf('word') >= 0 ||
          tipo.indexOf('excel') >= 0 ||
          tipo.indexOf('sheet') >= 0 ||
          tipo.indexOf('text') >= 0 ||
          tipo.indexOf('document') >= 0
        );

        if (ehDocumento) {
          try {
            var bytes = att.getBytes();
            var base64 = Utilities.base64Encode(bytes);
            email.anexos.push({
              nome: nomeAnexo,
              tipo: tipo,
              dados_base64: base64
            });
            Logger.log('Anexo incluido: ' + nomeAnexo + ' (' + bytes.length + ' bytes)');
          } catch(e) {
            Logger.log('Erro no anexo ' + nomeAnexo + ': ' + e);
            // Mesmo com erro, registra o nome do arquivo
            email.anexos.push({nome: nomeAnexo, tipo: tipo, dados_base64: ''});
          }
        }
      }

      emails.push(email);
      msg.markRead();
      Logger.log('Email capturado: ' + email.assunto +
                 ' | Corpo: ' + textoCorpo.length + ' chars' +
                 ' | Anexos: ' + email.anexos.length);
    }
  }

  if (emails.length === 0) {
    Logger.log('Nenhum email novo para processar.');
    props.setProperty('ultima_verificacao', new Date().toISOString());
    return;
  }

  // Envia para o sistema em blocos de 5 (evita timeout)
  var tamanhoBloco = 5;
  for (var b = 0; b < emails.length; b += tamanhoBloco) {
    var bloco = emails.slice(b, b + tamanhoBloco);
    var options = {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({token: TOKEN_SEGURANCA, emails: bloco}),
      headers: {'X-Webhook-Token': TOKEN_SEGURANCA},
      muteHttpExceptions: true
    };
    try {
      var response = UrlFetchApp.fetch(SISTEMA_URL, options);
      var resultado = JSON.parse(response.getContentText());
      Logger.log('Bloco enviado: ' + resultado.mensagem);
    } catch(e) {
      Logger.log('Erro ao enviar bloco: ' + e.toString());
    }
  }

  props.setProperty('ultima_verificacao', new Date().toISOString());
  Logger.log('Concluido! ' + emails.length + ' email(s) processado(s).');
}

function formatarData(data) {
  var mes = data.getMonth() + 1;
  var dia = data.getDate();
  return data.getFullYear() + '/' + (mes<10?'0'+mes:mes) + '/' + (dia<10?'0'+dia:dia);
}

function configurarGatilho() {
  var gatilhos = ScriptApp.getProjectTriggers();
  for (var i = 0; i < gatilhos.length; i++) {
    ScriptApp.deleteTrigger(gatilhos[i]);
  }
  ScriptApp.newTrigger('buscarEmailsNovos').timeBased().everyHours(1).create();
  Logger.log('Gatilho configurado: roda a cada hora automaticamente.');
}
