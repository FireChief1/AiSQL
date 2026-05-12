const $ = (s) => document.querySelector(s);
const chatEl = $('#chat');
const meUser = $('#me-user');
const meRole = $('#me-role');
const meInitial = $('#me-initial');
const roleBadge = $('#role-badge');
const roleTables = $('#role-tables');
const auditEl = $('#audit');
const threadsListEl = $('#threads-list');
const askForm = $('#ask-form');
const questionEl = $('#question');
const askBtn = $('#btn-ask');

// ---------------------------------------------------------------------------
// Toast notifications. Right-top stack, 3s auto-dismiss.
// ---------------------------------------------------------------------------
function showToast(message, kind = 'info') {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    host.className = 'fixed top-4 right-4 z-[60] space-y-2 max-w-sm pointer-events-none';
    document.body.appendChild(host);
  }
  const tone = {
    info:    'border-violet-400/30 bg-violet-500/10 text-violet-100',
    success: 'border-emerald-400/30 bg-emerald-500/10 text-emerald-100',
    error:   'border-red-400/30 bg-red-500/10 text-red-100',
    warn:    'border-amber-400/30 bg-amber-500/10 text-amber-100',
  }[kind] || 'border-white/10 bg-black/40 text-gray-200';
  const el = document.createElement('div');
  el.className = `pointer-events-auto glass border ${tone} rounded-lg px-3 py-2 text-xs fade-in shadow-lg`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
    el.style.opacity = '0';
    el.style.transform = 'translateX(20px)';
    setTimeout(() => el.remove(), 250);
  }, 3000);
}

// ---------------------------------------------------------------------------
// SQL block: copy-to-clipboard button rendered next to the <pre>.
// ---------------------------------------------------------------------------
function sqlBlockHtml(sql) {
  const id = 'sql-' + Math.random().toString(36).slice(2, 10);
  return `
    <div class="relative group/sql mt-2">
      <pre id="${id}" class="mono text-xs p-2 pr-10 rounded bg-black/40 overflow-x-auto"><code>${highlightSql(sql)}</code></pre>
      <button
        data-copy-id="${id}"
        class="absolute top-1.5 right-1.5 opacity-0 group-hover/sql:opacity-100 transition px-2 py-1 rounded bg-violet-500/30 hover:bg-violet-500/50 text-[10px] text-violet-100"
        title="Kopyala"
      >COPY</button>
    </div>
  `;
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy-id]');
  if (!btn) return;
  const pre = document.getElementById(btn.dataset.copyId);
  if (!pre) return;
  const text = pre.textContent || '';
  navigator.clipboard.writeText(text).then(
    () => showToast('SQL panoya kopyalandı', 'success'),
    () => showToast('Kopyalanamadı', 'error'),
  );
});

// ---------------------------------------------------------------------------
// Result table: render a list of row objects as a compact HTML table.
// ---------------------------------------------------------------------------
function rowsTableHtml(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return '';
  const cols = Array.from(
    rows.reduce((s, r) => {
      Object.keys(r || {}).forEach((k) => s.add(k));
      return s;
    }, new Set())
  );
  if (!cols.length) return '';

  const header = cols
    .map((c) => `<th class="px-3 py-1.5 text-left text-[10px] uppercase tracking-wider text-violet-300 font-semibold border-b border-white/10">${escapeHtml(c)}</th>`)
    .join('');
  const body = rows
    .map((r) => `<tr class="border-b border-white/5 hover:bg-white/[0.02] transition">
      ${cols.map((c) => `<td class="px-3 py-1.5 text-xs text-gray-200 mono">${escapeHtml(formatCell(r[c]))}</td>`).join('')}
    </tr>`)
    .join('');

  return `
    <div class="mt-3 rounded-lg overflow-hidden border border-white/10 bg-black/30">
      <div class="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-400 bg-white/[0.03] border-b border-white/10 flex items-center justify-between">
        <span>Sonuc — ${rows.length} satir</span>
      </div>
      <div class="overflow-x-auto max-h-72 overflow-y-auto scroll-thin">
        <table class="w-full">
          <thead class="sticky top-0 bg-black/60"><tr>${header}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>
  `;
}

function formatCell(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

const TOKEN_KEY = 'aisql.token';

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }

function authHeader() {
  const t = getToken();
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}

async function ensureLogin() {
  if (!getToken()) return showLoginOverlay();
  try {
    const r = await fetch('/api/me', { headers: authHeader() });
    if (!r.ok) throw new Error('unauth');
    const me = await r.json();
    return me;
  } catch {
    clearToken();
    return showLoginOverlay();
  }
}

function showLoginOverlay() {
  return new Promise((resolve) => {
    const html = `
      <div id="login-overlay" class="fixed inset-0 bg-black/80 backdrop-blur-md flex items-center justify-center z-50">
        <div class="glass gradient-border rounded-2xl p-8 w-[400px] max-w-[90vw]">
          <h2 class="text-2xl font-semibold mb-2"><span class="gradient-text">AiSQL</span></h2>
          <p class="text-xs text-gray-500 mb-5">Demo kullanicilari: admin / analyst_user / sales_user / support_user / guest. Sifre: &lt;user&gt;_demo_pw</p>
          <input id="li-user" placeholder="user_id" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm mb-2 focus:outline-none focus:border-violet-400" />
          <input id="li-pw" type="password" placeholder="password" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm mb-3 focus:outline-none focus:border-violet-400" />
          <button id="li-submit" class="w-full px-4 py-2.5 rounded-lg bg-gradient-to-br from-violet-500 to-blue-500 hover:from-violet-400 hover:to-blue-400 font-medium text-sm transition">Giris</button>
          <p id="li-err" class="text-xs text-red-400 mt-3 hidden"></p>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML('beforeend', html);
    const tryLogin = async () => {
      const user_id = $('#li-user').value.trim();
      const password = $('#li-pw').value;
      if (!user_id || !password) return;
      try {
        const r = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id, password }),
        });
        if (!r.ok) {
          const e = await r.json();
          $('#li-err').textContent = e.detail || 'Login failed';
          $('#li-err').classList.remove('hidden');
          return;
        }
        const data = await r.json();
        setToken(data.access_token);
        document.getElementById('login-overlay').remove();
        resolve({
          id: data.user_id,
          role: data.role,
          allowed_tables: data.allowed_tables || [],
        });
      } catch (err) {
        $('#li-err').textContent = String(err);
        $('#li-err').classList.remove('hidden');
      }
    };
    $('#li-submit').addEventListener('click', tryLogin);
    $('#li-pw').addEventListener('keydown', (e) => { if (e.key === 'Enter') tryLogin(); });
    setTimeout(() => $('#li-user').focus(), 100);
  });
}

const EXAMPLES = {
  guest:        ['How many artists are there?', 'List 5 albums', 'artistleri sil', 'List all customers with emails'],
  sales_user:   ['How many customers are there?', 'List 5 invoices', 'DROP TABLE invoice', 'How many artists are there?'],
  analyst_user: ['How many tracks are there?', 'Top 5 longest tracks', "UPDATE track SET name='hacked'", 'Show me all customer phone numbers'],
  support_user: ['How many customers are there?', 'TRUNCATE customer', 'List all tracks'],
  admin:        ['How many tracks are there?', 'How many albums are there?', 'DELETE FROM artist'],
};

let me = null;
let currentThreadId = null;
let chatHasMessages = false;
const threadBadge = $('#thread-badge');

// Thread IDs are minted by the server when the first message of a new
// conversation arrives, so the client never needs to generate one.

function refreshThreadBadge() {
  const tid = currentThreadId;
  if (!tid) {
    threadBadge.textContent = 'yeni konusma';
    threadBadge.classList.remove('bg-violet-500/10','text-violet-300','border-violet-500/20');
    threadBadge.classList.add('bg-emerald-500/10','text-emerald-300','border-emerald-500/20');
    return;
  }
  threadBadge.textContent = `thread: ${tid}`;
  threadBadge.classList.remove('bg-emerald-500/10','text-emerald-300','border-emerald-500/20');
  threadBadge.classList.add('bg-violet-500/10','text-violet-300','border-violet-500/20');
}

function escapeHtml(text) {
  return String(text ?? '')
    .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
    .replaceAll('"','&quot;').replaceAll("'", '&#39;');
}

function highlightSql(text) {
  const kw = /\b(SELECT|FROM|JOIN|LEFT|RIGHT|INNER|OUTER|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|AS|ON|AND|OR|NOT|IN|LIKE|IS|NULL|COUNT|SUM|AVG|MAX|MIN|DISTINCT|UNION|WITH)\b/gi;
  return escapeHtml(text).replace(kw, '<span class="text-violet-400 font-medium">$1</span>');
}

function applyMe(u) {
  me = u;
  currentThreadId = null;
  meUser.textContent = u.id;
  meRole.textContent = u.role;
  meInitial.textContent = (u.id || '?').charAt(0).toUpperCase();
  roleBadge.textContent = u.role;
  const tables = Array.isArray(u.allowed_tables) ? u.allowed_tables : [];
  roleTables.innerHTML = (tables.includes('*')
    ? ['tum tablolar']
    : tables
  ).map((t) => `<span class="text-[10px] mono px-1.5 py-0.5 rounded bg-white/5 text-gray-300">${escapeHtml(t)}</span>`).join('');

  refreshThreadBadge();
}

function clearWelcome() {
  if (!chatHasMessages) { chatEl.innerHTML = ''; chatHasMessages = true; }
}

function showWelcome() {
  const suggestions = [
    'Kaç sanatçı var?',
    'Top 5 albüm by satış geliri',
    'En uzun süren 3 şarkı',
    'Ülkeye göre müşteri sayısı',
  ];
  chatEl.innerHTML = `
    <div class="text-center px-6 py-12 fade-in">
      <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-500/20 to-blue-500/10 border border-violet-400/30 mb-4">
        <svg class="w-8 h-8 text-violet-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 7v10c0 2 1.5 3 4 3h8c2.5 0 4-1 4-3V7c0-2-1.5-3-4-3H8C5.5 4 4 5 4 7zM4 11h16M9 7v14M15 7v14"/>
        </svg>
      </div>
      <h3 class="text-base font-semibold text-gray-100 mb-1">Chinook veritabaniyla konus</h3>
      <p class="text-xs text-gray-500 mb-6">Bir soru yaz, ya da asagidaki ornek sorgulardan birini sec.</p>
      <div class="flex flex-wrap gap-2 justify-center max-w-md mx-auto">
        ${suggestions.map((s) => `
          <button data-suggestion="${escapeHtml(s)}" class="text-xs px-3 py-1.5 rounded-full bg-white/5 hover:bg-violet-500/20 border border-white/10 hover:border-violet-400/40 text-gray-300 transition">
            ${escapeHtml(s)}
          </button>
        `).join('')}
      </div>
      <p class="text-[10px] text-gray-600 mt-6">⌘K ile yeni konusma · Hover ile SQL kopyala</p>
    </div>
  `;
  chatEl.querySelectorAll('[data-suggestion]').forEach((b) => {
    b.addEventListener('click', () => {
      questionEl.value = b.dataset.suggestion;
      questionEl.focus();
    });
  });
  chatHasMessages = false;
}

let _threadsCache = [];

function _renderFilteredThreads(filter = '') {
  const q = filter.trim().toLowerCase();
  const threads = q
    ? _threadsCache.filter((t) => (t.title || '').toLowerCase().includes(q))
    : _threadsCache;

  if (!_threadsCache.length) {
    threadsListEl.innerHTML = `
      <div class="text-center py-8 px-4 fade-in">
        <svg class="w-10 h-10 mx-auto mb-2 text-gray-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.86 9.86 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
        </svg>
        <p class="text-xs text-gray-500">Henuz konusma yok</p>
        <p class="text-[10px] text-gray-600 mt-1">Bir soru sorarak baslat</p>
      </div>`;
    return;
  }
  if (!threads.length) {
    threadsListEl.innerHTML = `
      <div class="text-center py-6 px-4 text-xs text-gray-500">
        '${escapeHtml(filter)}' ile eslesen konusma yok
      </div>`;
    return;
  }

  threadsListEl.innerHTML = threads.map((t) => {
    const active = t.thread_id === currentThreadId ? 'bg-violet-500/20 border-violet-400/40' : 'bg-transparent border-transparent hover:bg-white/5';
    const time = new Date(t.updated_at);
    const timeStr = time.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' }) + ' ' + time.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
    return `
      <div class="group flex items-center gap-1 rounded-lg border px-2 py-1.5 transition cursor-pointer ${active}" data-tid="${escapeHtml(t.thread_id)}">
        <div class="flex-1 min-w-0 thread-pick">
          <div class="text-xs text-gray-200 truncate">${escapeHtml(t.title)}</div>
          <div class="text-[10px] text-gray-500">${escapeHtml(timeStr)}</div>
        </div>
        <button data-del="${escapeHtml(t.thread_id)}" title="Sil" class="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 transition p-1">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
        </button>
      </div>
    `;
  }).join('');

  threadsListEl.querySelectorAll('[data-tid]').forEach((row) => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('[data-del]')) return;
      selectThread(row.dataset.tid);
    });
  });
  threadsListEl.querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const tid = btn.dataset.del;
      if (!confirm('Bu konusmayi silmek istediğine emin misin?')) return;
      const r = await fetch('/api/threads/' + encodeURIComponent(tid), {
        method: 'DELETE', headers: authHeader(),
      });
      if (r.ok) showToast('Konusma silindi', 'success');
      else showToast('Silinemedi', 'error');
      if (tid === currentThreadId) {
        currentThreadId = null;
        showWelcome();
        refreshThreadBadge();
      }
      loadThreads();
    });
  });
}

async function loadThreads(selectId = null) {
  try {
    const r = await fetch('/api/threads?limit=50', { headers: authHeader() });
    if (!r.ok) throw new Error(await r.text());
    _threadsCache = await r.json();
    const searchEl = document.getElementById('threads-search');
    _renderFilteredThreads(searchEl ? searchEl.value : '');
    if (selectId) selectThread(selectId);
  } catch (e) {
    threadsListEl.innerHTML = `<div class="text-red-400 text-xs p-3">${escapeHtml(String(e))}</div>`;
  }
}

async function selectThread(tid) {
  if (!tid) return;
  currentThreadId = tid;
  refreshThreadBadge();
  // Mark active row visually without a full reload.
  threadsListEl.querySelectorAll('[data-tid]').forEach((row) => {
    const isActive = row.dataset.tid === tid;
    row.classList.toggle('bg-violet-500/20', isActive);
    row.classList.toggle('border-violet-400/40', isActive);
    row.classList.toggle('hover:bg-white/5', !isActive);
    row.classList.toggle('border-transparent', !isActive);
  });
  await loadThreadMessages(tid);
}

async function loadThreadMessages(tid) {
  chatEl.innerHTML = '<div class="text-center text-gray-500 text-xs py-6 fade-in">Konusma yukleniyor...</div>';
  try {
    const r = await fetch('/api/threads/' + encodeURIComponent(tid) + '/messages', { headers: authHeader() });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const msgs = data.messages || [];
    if (!msgs.length) { showWelcome(); return; }
    chatEl.innerHTML = '';
    chatHasMessages = true;
    for (const m of msgs) {
      if (m.role === 'HumanMessage') {
        addUserMsg(m.content, me.id);
      } else {
        const text = m.content;
        const sqlMatch = text.match(/```sql\s*([\s\S]+?)\s*```/);
        const sql = sqlMatch ? sqlMatch[1].trim() : '';
        const plain = text.replace(/```sql[\s\S]+?```/, '').trim();
        chatEl.insertAdjacentHTML('beforeend', `
          <div class="fade-in flex">
            <div class="max-w-[90%] rounded-2xl rounded-tl-sm border-l-4 border-emerald-500/60 bg-emerald-500/5 border border-emerald-500/20 px-4 py-3">
              <div class="text-[10px] uppercase tracking-wider font-semibold text-emerald-400 mb-2">Agent</div>
              <div class="text-sm text-gray-100 whitespace-pre-wrap">${escapeHtml(plain)}</div>
              ${sql ? sqlBlockHtml(sql) : ''}
            </div>
          </div>
        `);
      }
    }
    chatEl.scrollTop = chatEl.scrollHeight;
  } catch (e) {
    chatEl.innerHTML = `<div class="text-red-400 text-xs p-3">${escapeHtml(String(e))}</div>`;
  }
}

function addUserMsg(text, userId) {
  clearWelcome();
  const html = `
    <div class="fade-in flex justify-end">
      <div class="max-w-[80%] bg-gradient-to-br from-violet-500/20 to-blue-500/10 border border-violet-400/30 rounded-2xl rounded-tr-sm px-4 py-2.5">
        <div class="text-[10px] uppercase tracking-wider text-violet-300 font-semibold mb-0.5">${escapeHtml(userId)}</div>
        <div class="text-sm whitespace-pre-wrap">${escapeHtml(text)}</div>
      </div>
    </div>
  `;
  chatEl.insertAdjacentHTML('beforeend', html);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addLoadingMsg() {
  const id = 'loading-' + Math.random().toString(36).slice(2);
  const html = `
    <div id="${id}" class="fade-in flex">
      <div class="max-w-[80%] glass border border-white/10 rounded-2xl rounded-tl-sm px-4 py-2.5">
        <div class="text-[10px] uppercase tracking-wider text-gray-400 font-semibold mb-0.5">Agent</div>
        <div class="text-sm text-gray-400 typing">Dusunuyor</div>
      </div>
    </div>
  `;
  chatEl.insertAdjacentHTML('beforeend', html);
  chatEl.scrollTop = chatEl.scrollHeight;
  return id;
}

function renderBotMsg(data) {
  const blocked = data.result.blocked;
  if (blocked) {
    return `
      <div class="fade-in flex">
        <div class="max-w-[90%] rounded-2xl rounded-tl-sm border-l-4 border-red-500 bg-red-500/5 border border-red-500/20 px-4 py-3">
          <div class="flex items-center gap-2 mb-2">
            <span class="text-[10px] uppercase tracking-wider font-semibold text-red-400">BLOCKED</span>
            ${data.result.category ? `<span class="text-[10px] mono px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">${escapeHtml(data.result.category)}</span>`: ''}
            ${data.result.risk ? `<span class="text-[10px] mono px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-300">risk: ${escapeHtml(data.result.risk)}</span>`: ''}
          </div>
          <div class="text-sm text-gray-200 mb-2">${escapeHtml(data.result.reason || 'Bloklandi.')}</div>
          <details class="text-xs text-gray-400">
            <summary class="cursor-pointer hover:text-gray-200">Tam yanit</summary>
            <pre class="mono mt-2 p-2 rounded bg-black/40 whitespace-pre-wrap">${escapeHtml(data.result.raw)}</pre>
          </details>
        </div>
      </div>
    `;
  }

  const sqlMatch = data.result.raw.match(/```sql\s*([\s\S]+?)\s*```/);
  const sql = sqlMatch ? sqlMatch[1].trim() : '';
  const text = data.result.raw.replace(/```sql[\s\S]+?```/, '').trim();
  const tableHtml = rowsTableHtml(data.result.rows);
  return `
    <div class="fade-in flex">
      <div class="max-w-[90%] rounded-2xl rounded-tl-sm border-l-4 border-emerald-500 bg-emerald-500/5 border border-emerald-500/20 px-4 py-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[10px] uppercase tracking-wider font-semibold text-emerald-400">OK</span>
          <span class="text-[10px] text-gray-400">${escapeHtml(data.user)} / ${escapeHtml(data.role)}</span>
        </div>
        <div class="text-sm text-gray-100 whitespace-pre-wrap">${escapeHtml(text)}</div>
        ${tableHtml}
        ${sql ? sqlBlockHtml(sql) : ''}
      </div>
    </div>
  `;
}

async function loadAudit() {
  if (!me || me.role !== 'admin') return;
  auditEl.innerHTML = '<div class="text-center text-gray-500 py-6">Yukleniyor...</div>';
  try {
    const r = await fetch('/api/audit?limit=20', { headers: authHeader() });
    if (!r.ok) throw new Error(await r.text());
    const rows = await r.json();
    if (!rows.length) {
      auditEl.innerHTML = '<div class="text-center text-gray-500 py-6">Henuz kayit yok.</div>';
      return;
    }
    auditEl.innerHTML = rows.map((row) => {
      const cat = row.category || 'unknown';
      const catColor = cat.includes('write')   ? 'bg-red-500/20 text-red-300'
                      : cat.includes('pii')     ? 'bg-pink-500/20 text-pink-300'
                      : cat.includes('permission') ? 'bg-amber-500/20 text-amber-300'
                      : 'bg-gray-500/20 text-gray-300';
      return `
        <div class="fade-in border border-white/5 rounded-lg p-2.5 bg-black/20 hover:bg-black/30 transition">
          <div class="flex items-center justify-between mb-1">
            <span class="mono text-[10px] text-gray-500">#${row.id}</span>
            <span class="text-[10px] mono px-1.5 py-0.5 rounded ${catColor}">${escapeHtml(cat)}</span>
          </div>
          <div class="text-xs text-gray-300 mb-1 line-clamp-2">${escapeHtml(row.user_prompt)}</div>
          <div class="flex items-center gap-2 text-[10px] text-gray-500">
            <span class="mono">${escapeHtml(row.user_id)}</span>
            <span>·</span>
            <span>${escapeHtml(row.user_role)}</span>
            ${row.tables ? `<span>·</span><span class="mono">${escapeHtml(row.tables)}</span>` : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    auditEl.innerHTML = `<div class="text-red-400 text-xs p-3">Audit log okunamadi: ${escapeHtml(String(e))}</div>`;
  }
}

async function ping() {
  try {
    const r = await fetch('/api/health');
    document.getElementById('dot-mcp').style.color = r.ok ? '#34d399' : '#ef4444';
  } catch { document.getElementById('dot-mcp').style.color = '#ef4444'; }

  // DB ping uses an authenticated endpoint everyone can hit.
  try {
    const r = await fetch('/api/me', { headers: authHeader() });
    document.getElementById('dot-db').style.color = r.ok ? '#34d399' : '#ef4444';
  } catch { document.getElementById('dot-db').style.color = '#ef4444'; }
  document.getElementById('dot-llm').style.color = '#34d399';
}

$('#btn-refresh').addEventListener('click', () => {
  loadAudit();
  showToast('Audit log yenilendi', 'info');
});
$('#btn-logout').addEventListener('click', async () => {
  // Tell the server to revoke this token, then clear the client state.
  try {
    await fetch('/api/auth/logout', { method: 'POST', headers: authHeader() });
    showToast('Cikis yapildi', 'success');
  } catch (e) { /* best-effort; we will clear the client either way */ }
  clearToken();
  setTimeout(() => location.reload(), 400);
});
$('#btn-threads-refresh').addEventListener('click', () => {
  loadThreads();
  showToast('Konusmalar yenilendi', 'info');
});
$('#btn-new-conv').addEventListener('click', () => {
  currentThreadId = null;
  showWelcome();
  refreshThreadBadge();
  threadsListEl.querySelectorAll('[data-tid]').forEach((row) => {
    row.classList.remove('bg-violet-500/20', 'border-violet-400/40');
    row.classList.add('hover:bg-white/5', 'border-transparent');
  });
  questionEl.focus();
});

askForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = questionEl.value.trim();
  if (!q || !me) return;
  questionEl.value = '';
  askBtn.disabled = true;
  addUserMsg(q, me.id);
  const loadingId = addLoadingMsg();

  try {
    const r = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeader() },
      body: JSON.stringify({
        question: q,
        thread_id: currentThreadId, // null on first turn -> server mints one
      }),
    });
    if (r.status === 401) {
      clearToken();
      location.reload();
      return;
    }
    const data = await r.json();
    document.getElementById(loadingId).remove();
    if (!r.ok) {
      chatEl.insertAdjacentHTML('beforeend', `
        <div class="fade-in flex">
          <div class="max-w-[80%] rounded-2xl border-l-4 border-red-500 bg-red-500/10 px-4 py-3">
            <div class="text-xs text-red-400 font-semibold mb-1">HATA</div>
            <div class="text-sm">${escapeHtml(data.detail || JSON.stringify(data))}</div>
          </div>
        </div>
      `);
    } else {
      chatEl.insertAdjacentHTML('beforeend', renderBotMsg(data));
      // Sync thread state with whatever the server confirmed.
      if (data.thread_id && data.thread_id !== currentThreadId) {
        currentThreadId = data.thread_id;
        refreshThreadBadge();
      }
      // Refresh ONLY the sidebar — do NOT pass selectId here because
      // selectThread() would reload messages from the DB and clobber the
      // just-rendered result table (rows aren't persisted in checkpoints).
      loadThreads();
      if (me.role === 'admin') loadAudit();
    }
    chatEl.scrollTop = chatEl.scrollHeight;
  } catch (err) {
    document.getElementById(loadingId).remove();
    chatEl.insertAdjacentHTML('beforeend', `
      <div class="fade-in flex">
        <div class="max-w-[80%] rounded-2xl border-l-4 border-red-500 bg-red-500/10 px-4 py-3">
          <div class="text-xs text-red-400 font-semibold mb-1">HATA</div>
          <div class="text-sm">${escapeHtml(String(err))}</div>
        </div>
      </div>
    `);
  } finally {
    askBtn.disabled = false;
    questionEl.focus();
  }
});

// Sidebar search: filter the cached thread list as the user types.
const _searchEl = document.getElementById('threads-search');
if (_searchEl) {
  _searchEl.addEventListener('input', (e) => _renderFilteredThreads(e.target.value));
}

// Keyboard shortcut: Cmd/Ctrl+K starts a new conversation.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    $('#btn-new-conv').click();
    showToast('Yeni konusma baslatildi', 'info');
  }
});

(async () => {
  const u = await ensureLogin();
  applyMe(u);
  loadThreads();
  if (u.role === 'admin') loadAudit();
  else auditEl.innerHTML = `
    <div class="text-center py-10 px-4 fade-in">
      <svg class="w-10 h-10 mx-auto mb-2 text-gray-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
      </svg>
      <p class="text-xs text-gray-500">Sadece admin icin</p>
    </div>`;
  ping();
  setInterval(ping, 15000);
})();
