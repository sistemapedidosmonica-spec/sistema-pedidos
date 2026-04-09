// ===================== SIDEBAR =====================
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('collapsed');
  document.getElementById('main-content').classList.toggle('expanded');
}

// ===================== TOAST =====================
function toast(msg, tipo = 'success') {
  const el = document.getElementById('toastEl');
  const txt = document.getElementById('toastText');
  txt.textContent = msg;
  el.className = 'toast align-items-center border-0';
  if (tipo === 'error')   el.classList.add('text-bg-danger');
  else if (tipo === 'warning') el.classList.add('text-bg-warning');
  else el.classList.add('text-bg-success');
  bootstrap.Toast.getOrCreateInstance(el, { delay: 4000 }).show();
}

// ===================== LOADING =====================
function showLoading(msg = 'Processando...', sub = '') {
  document.getElementById('loadingMsg').textContent = msg;
  document.getElementById('loadingSubMsg').textContent = sub;
  document.getElementById('loadingOverlay').style.display = 'flex';
}
function hideLoading() {
  document.getElementById('loadingOverlay').style.display = 'none';
}

// ===================== API =====================
async function api(url, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.erro || data.error || 'Erro desconhecido');
  return data;
}

// ===================== UTILS =====================
function copiarTexto(texto) {
  navigator.clipboard.writeText(texto)
    .then(() => toast('Copiado para a área de transferência!'))
    .catch(() => {
      const ta = document.createElement('textarea');
      ta.value = texto;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      toast('Copiado!');
    });
}

function abrirWhatsApp(telefone, texto) {
  const num = telefone.replace(/\D/g, '');
  window.open(`https://wa.me/55${num}?text=${encodeURIComponent(texto)}`, '_blank');
}

function formatarValor(v) {
  if (v === null || v === undefined) return '—';
  return 'R$ ' + Number(v).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatarQtd(q) {
  if (q === null || q === undefined) return '—';
  const n = Number(q);
  return Number.isInteger(n) ? n.toString() : n.toFixed(2).replace('.', ',');
}
