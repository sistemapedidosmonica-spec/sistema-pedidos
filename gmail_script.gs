/**
 * Script para leitura automática de emails do Gmail
 * e envio para o Sistema de Pedidos da Mônica.
 *
 * COMO USAR:
 * 1. Abra script.google.com
 * 2. Crie um novo projeto
 * 3. Cole este código
 * 4. Clique em Executar > buscarEmailsNovos
 * 5. Autorize o acesso
 * 6. Configure o gatilho para rodar a cada hora
 */

// ============================================================
// CONFIGURAÇÃO — altere apenas estas duas linhas se necessário
// ============================================================
var SISTEMA_URL = 'https://monicapedidos.pythonanywhere.com/api/webhook/email';
var TOKEN_SEGURANCA = 'monica2024';
// ============================================================

function buscarEmailsNovos() {
  var props = PropertiesService.getScriptProperties();
  var ultimaData = props.getProperty('ultima_verificacao');

  // Na primeira vez, pega emails dos últimos 7 dias
  var dataInicio;
  if (!ultimaData) {
    dataInicio = new Date();
    dataInicio.setDate(dataInicio.getDate() - 7);
  } else {
    dataInicio = new Date(ultimaData);
  }

  // Busca emails não lidos desde a última verificação
  var query = 'is:unread after:' + formatarData(dataInicio);
  var threads = GmailApp.search(query, 0, 50);

  if (threads.length === 0) {
    Logger.log('Nenhum email novo encontrado.');
    props.setProperty('ultima_verificacao', new Date().toISOString());
    return;
  }

  var emails = [];

  for (var i = 0; i < threads.length; i++) {
    var messages = threads[i].getMessages();
    for (var j = 0; j < messages.length; j++) {
      var msg = messages[j];

      // Pula se já foi lido antes da última verificação
      if (msg.getDate() < dataInicio) continue;

      var email = {
        email_id: msg.getId(),
        assunto: msg.getSubject() || '',
        email_remetente: msg.getFrom() || '',
        texto_corpo: msg.getPlainBody() || msg.getBody() || '',
        data_recebimento: msg.getDate().toISOString(),
        anexos: []
      };

      // Processa anexos (PDF, Word, etc)
      var attachments = msg.getAttachments();
      for (var k = 0; k < attachments.length; k++) {
        var att = attachments[k];
        var nome = att.getName().toLowerCase();
        if (nome.endsWith('.pdf') || nome.endsWith('.doc') ||
            nome.endsWith('.docx') || nome.endsWith('.xls') ||
            nome.endsWith('.xlsx')) {
          email.anexos.push({
            nome: att.getName(),
            tipo: att.getContentType(),
            tamanho: att.getSize()
          });
        }
      }

      // Limita corpo a 10.000 caracteres
      if (email.texto_corpo.length > 10000) {
        email.texto_corpo = email.texto_corpo.substring(0, 10000);
      }

      emails.push(email);

      // Marca como lido
      msg.markRead();
    }
  }

  if (emails.length === 0) {
    Logger.log('Nenhum email novo para processar.');
    props.setProperty('ultima_verificacao', new Date().toISOString());
    return;
  }

  // Envia para o sistema
  var payload = {
    token: TOKEN_SEGURANCA,
    emails: emails
  };

  var options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    headers: {
      'X-Webhook-Token': TOKEN_SEGURANCA
    },
    muteHttpExceptions: true
  };

  try {
    var response = UrlFetchApp.fetch(SISTEMA_URL, options);
    var resultado = JSON.parse(response.getContentText());
    Logger.log('Enviado com sucesso: ' + resultado.mensagem);
    Logger.log(emails.length + ' email(s) processado(s).');
  } catch (e) {
    Logger.log('Erro ao enviar para o sistema: ' + e.toString());
  }

  // Salva timestamp da última verificação
  props.setProperty('ultima_verificacao', new Date().toISOString());
}

function formatarData(data) {
  var ano = data.getFullYear();
  var mes = data.getMonth() + 1;
  var dia = data.getDate();
  return ano + '/' + (mes < 10 ? '0' + mes : mes) + '/' + (dia < 10 ? '0' + dia : dia);
}

// Configura gatilho automático para rodar a cada hora
function configurarGatilho() {
  // Remove gatilhos existentes para evitar duplicatas
  var gatilhos = ScriptApp.getProjectTriggers();
  for (var i = 0; i < gatilhos.length; i++) {
    ScriptApp.deleteTrigger(gatilhos[i]);
  }

  // Cria novo gatilho: a cada hora
  ScriptApp.newTrigger('buscarEmailsNovos')
    .timeBased()
    .everyHours(1)
    .create();

  Logger.log('Gatilho configurado! O sistema vai verificar emails a cada hora automaticamente.');
}
