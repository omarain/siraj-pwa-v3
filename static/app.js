// Siraj PWA v3 — Workshop Chat Engine
const API = '/api';
let currentChat = null;
let chats = {};
let exchangeCount = 0;
let isStreaming = false;

// ── Init ─────────────────────────────────────────────────────
(async function init() {
  await checkAuth();
  await loadChats();
  updateClock(); setInterval(updateClock, 30000);
})();

async function checkAuth() {
  try {
    const r = await fetch(API + '/me');
    if (!r.ok) { window.location.href = '/login'; return; }
    const d = await r.json();
    const name = d.user?.email?.split('@')[0] || 'Siraj';
    document.getElementById('footName').textContent = name;
    document.getElementById('avatarLetter').textContent = name[0].toUpperCase();
  } catch(e) {
    // Keep trying — might be offline
  }
}

async function doLogout() {
  await fetch(API + '/logout', { method: 'POST' });
  localStorage.clear();
  window.location.href = '/login';
}

function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

// ── Chat List ────────────────────────────────────────────────
async function loadChats() {
  try {
    const r = await fetch(API + '/chats');
    if (!r.ok) return;
    const d = await r.json();
    chats = {};
    d.chats.forEach(c => { chats[c.id] = c; });
    renderChatList();
    // Auto-select last active or create default
    if (d.chats.length > 0) {
      selectChat(d.chats[0].id);
    } else {
      await newChat();
    }
  } catch(e) { console.error('loadChats', e); }
}

function renderChatList() {
  const list = document.getElementById('chatList');
  const count = document.getElementById('chatCount');
  const items = Object.values(chats).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  count.textContent = items.length;
  list.innerHTML = items.map(c => {
    const active = currentChat === c.id ? ' active' : '';
    const dots = ['d-teal', 'd-indigo', 'd-gold'][Math.abs(hashStr(c.id)) % 3];
    return `<div class="sess-card${active}" onclick="selectChat('${c.id}')" title="${c.name}">
      <div class="sess-body">
        <div class="sess-name"><span class="tag-dot ${dots}"></span>${escHtml(c.name)}</div>
        <div class="sess-time">${timeAgo(c.updated_at)}</div>
      </div>
    </div>`;
  }).join('');
}

async function newChat() {
  try {
    const r = await fetch(API + '/chats', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'New Chat' }) });
    if (!r.ok) return;
    const d = await r.json();
    chats[d.chat.id] = d.chat;
    selectChat(d.chat.id);
    renderChatList();
  } catch(e) { console.error('newChat', e); }
}

function selectChat(id) {
  if (isStreaming) return; // Don't switch mid-stream
  currentChat = id;
  renderChatList();
  document.getElementById('chatTitle').textContent = chats[id]?.name || 'New Chat';
  loadMessages(id);
}

async function loadMessages(id) {
  try {
    const r = await fetch(API + `/chats/${id}/messages`);
    if (!r.ok) { document.getElementById('msgs').innerHTML = ''; return; }
    const d = await r.json();
    const msgs = d.messages || [];
    exchangeCount = Math.floor(msgs.length / 2);
    document.getElementById('chatMeta').textContent = exchangeCount + ' exchanges';
    renderMessages(msgs);
  } catch(e) { console.error('loadMessages', e); }
}

// ── Messages Render ──────────────────────────────────────────
function renderMessages(msgs) {
  const container = document.getElementById('msgs');
  if (!msgs.length) {
    container.innerHTML = '<div class="msgs-inner"><div style="text-align:center;color:var(--slate);padding:3rem;font-family:Cormorant,serif;font-size:18px;font-style:italic">Start a conversation</div></div>';
    return;
  }
  // Group into pairs (user + assistant)
  let html = '<div class="msgs-inner">';
  let i = 0;
  while (i < msgs.length) {
    const userMsg = msgs[i]?.role === 'user' ? msgs[i] : null;
    const asstMsg = msgs[i + 1]?.role === 'assistant' ? msgs[i + 1] : null;
    if (userMsg || asstMsg) {
      const num = Math.floor(i / 2) + 1;
      html += renderPair(num, userMsg, asstMsg);
    }
    i += (userMsg && asstMsg) ? 2 : 1;
  }
  html += '</div>';
  container.innerHTML = html;
  scrollDown();
}

function renderPair(num, userMsg, asstMsg) {
  let h = `<div class="pair">
    <div class="pair-head">
      <div class="ph-left"><span class="ph-num">#${String(num).padStart(2,'0')}</span> Exchange</div>
      <span class="status-badge">complete</span>
    </div>`;
  if (userMsg) {
    h += `<div class="user-msg"><span class="who">You</span>${escHtml(userMsg.content)}</div><div class="sep"></div>`;
  }
  if (asstMsg) {
    h += `<div class="asst">
      <div class="who"><span class="g">&#9764;</span> Siraj</div>
      <div class="prose">${formatContent(asstMsg.content)}</div>
    </div>`;
  }
  h += '</div>';
  return h;
}

// ── Send Message ─────────────────────────────────────────────
async function sendMessage() {
  if (isStreaming) return;
  const input = document.getElementById('textInput');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';

  // Collect messages for API
  const messages = collectMessages();
  messages.push({ role: 'user', content: text });

  // Show user message immediately
  const userMsg = { role: 'user', content: text };
  appendToCurrentView(userMsg);
  exchangeCount++;
  document.getElementById('chatMeta').textContent = exchangeCount + ' exchanges';

  // Stream assistant response
  isStreaming = true;
  document.getElementById('sendBtn').textContent = '…';
  document.getElementById('statusText').textContent = 'Reasoning…';
  document.getElementById('statusDot').style.background = '#e3b341';

  const asstDiv = createStreamingBubble();
  let fullText = '';

  try {
    const r = await fetch(API + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, chat_id: currentChat }),
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim() || !line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') break;
        try {
          const j = JSON.parse(data);
          const delta = j.choices?.[0]?.delta;
          if (delta?.tool_calls?.length) {
            const toolName = delta.tool_calls[0].function?.name || 'tools';
            document.getElementById('statusText').textContent = 'Using ' + toolName + '…';
          }
          if (delta?.content) {
            fullText += delta.content;
            asstDiv.querySelector('.prose').innerHTML = formatContent(fullText) + '<span class="cursor">▌</span>';
            scrollDown();
          }
        } catch(e) {}
      }
    }
  } catch(e) {
    fullText = 'Error: ' + e.message;
    asstDiv.querySelector('.prose').innerHTML = formatContent(fullText);
  }

  // Remove cursor, finalize
  asstDiv.querySelector('.prose').innerHTML = formatContent(fullText);
  document.querySelector('.cursor')?.remove();

  // Save messages
  const allMsgs = collectMessages();
  allMsgs.push({ role: 'assistant', content: fullText });
  await saveMessages(allMsgs);
  await updateChatTimestamp();

  isStreaming = false;
  document.getElementById('sendBtn').textContent = 'Send ↑';
  document.getElementById('statusText').textContent = 'Connected';
  document.getElementById('statusDot').style.background = '#5fb47e';
  document.getElementById('tokCount').textContent = '~' + Math.round(fullText.length / 4) + ' tok';
}

function collectMessages() {
  const msgs = [];
  const pairs = document.querySelectorAll('.pair');
  pairs.forEach(pair => {
    const userEl = pair.querySelector('.user-msg');
    const asstEl = pair.querySelector('.asst .prose');
    if (userEl) {
      const content = userEl.textContent.replace(/^You\s*/, '').trim();
      if (content) msgs.push({ role: 'user', content });
    }
    if (asstEl) {
      const content = asstEl.textContent.trim();
      if (content) msgs.push({ role: 'assistant', content });
    }
  });
  return msgs;
}

function appendToCurrentView(msg) {
  const container = document.getElementById('msgs');
  if (!container.querySelector('.msgs-inner')) {
    container.innerHTML = '<div class="msgs-inner"></div>';
  }
  const inner = container.querySelector('.msgs-inner');
  if (msg.role === 'user') {
    inner.innerHTML += renderPair(exchangeCount, msg, null);
  }
  scrollDown();
}

function createStreamingBubble() {
  const container = document.getElementById('msgs');
  const inner = container.querySelector('.msgs-inner');
  // Find last pair or create new
  let lastPair = inner.querySelector('.pair:last-child');
  if (!lastPair || lastPair.querySelector('.asst')) {
    // Create new pair
    inner.innerHTML += renderPair(exchangeCount, null, null);
    lastPair = inner.querySelector('.pair:last-child');
  }
  // Remove sep if no user msg in this pair
  if (!lastPair.querySelector('.user-msg')) {
    const sep = lastPair.querySelector('.sep');
    if (sep) sep.remove();
  }
  // Add asst section if not present
  if (!lastPair.querySelector('.asst')) {
    lastPair.innerHTML += `<div class="asst">
      <div class="who"><span class="g">&#9764;</span> Siraj</div>
      <div class="prose"></div>
    </div>`;
  }
  scrollDown();
  return lastPair.querySelector('.asst');
}

async function saveMessages(msgs) {
  if (!currentChat) return;
  try {
    await fetch(API + `/chats/${currentChat}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: msgs.slice(-200) }),
    });
  } catch(e) { console.error('saveMessages', e); }
}

async function updateChatTimestamp() {
  if (!currentChat || !chats[currentChat]) return;
  chats[currentChat].updated_at = new Date().toISOString();
  renderChatList();
}

// ── Helpers ──────────────────────────────────────────────────
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

function scrollDown() {
  const c = document.getElementById('msgsContainer');
  setTimeout(() => { c.scrollTop = c.scrollHeight; }, 50);
}

function formatContent(text) {
  if (!text) return '';
  // Basic markdown: bold, italic, code blocks, inline code, lists
  let html = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
  if (!html.startsWith('<')) html = '<p>' + html + '</p>';
  return html;
}

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function timeAgo(ts) {
  if (!ts) return '';
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  return Math.floor(hrs / 24) + 'd ago';
}

function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
  return h;
}

// ── Messaging Platform Connections ────────────────────────────

async function openMessaging() {
  document.getElementById('msgModal').style.display = 'flex';
  await refreshMessagingStatus();
}

function closeMessaging() {
  document.getElementById('msgModal').style.display = 'none';
}

async function refreshMessagingStatus() {
  try {
    const r = await fetch(API + '/messaging/status');
    if (!r.ok) return;
    const d = await r.json();
    for (const conn of d.connections || []) {
      if (conn.platform === 'telegram') {
        document.getElementById('tgConnectForm').style.display = 'none';
        document.getElementById('tgVerifyForm').style.display = 'none';
        document.getElementById('tgConnected').style.display = 'block';
        document.getElementById('tgStatus').textContent = 'Connected as ' + conn.platform_user_id;
        document.getElementById('tgStatus').style.color = 'var(--teal)';
        document.getElementById('msgStatus').textContent = 'Telegram linked ✓';
      }
    }
  } catch(e) {}
}
refreshMessagingStatus(); // check on load

async function connectTelegram() {
  const tgId = document.getElementById('tgIdInput').value.trim();
  if (!tgId) return alert('Enter your Telegram ID');
  
  const btn = document.getElementById('tgConnectBtn');
  btn.textContent = 'Sending…';
  btn.disabled = true;
  
  try {
    const r = await fetch(API + '/messaging/connect/telegram', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({telegram_id: tgId})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Failed');
    
    // Show OTP input
    document.getElementById('tgConnectForm').style.display = 'none';
    document.getElementById('tgVerifyForm').style.display = 'block';
    document.getElementById('tgStatus').textContent = 'OTP sent — check Telegram';
    document.getElementById('tgStatus').style.color = 'var(--gold)';
  } catch(e) {
    alert(e.message);
    btn.textContent = 'Send OTP';
    btn.disabled = false;
  }
}

async function verifyTelegram() {
  const tgId = document.getElementById('tgIdInput').value.trim();
  const otp = document.getElementById('tgOtpInput').value.trim();
  if (!otp || otp.length !== 6) return alert('Enter the 6-digit OTP');
  
  try {
    const r = await fetch(API + '/messaging/verify/telegram', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({telegram_id: tgId, otp})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Invalid OTP');
    
    document.getElementById('tgVerifyForm').style.display = 'none';
    document.getElementById('tgConnected').style.display = 'block';
    document.getElementById('tgStatus').textContent = 'Connected as ' + tgId;
    document.getElementById('tgStatus').style.color = 'var(--teal)';
    document.getElementById('msgStatus').textContent = 'Telegram linked ✓';
  } catch(e) {
    alert(e.message);
  }
}

async function disconnectPlatform(platform) {
  if (!confirm('Disconnect ' + platform + '?')) return;
  try {
    await fetch(API + '/messaging/disconnect/' + platform, {method: 'DELETE'});
    document.getElementById('tgConnected').style.display = 'none';
    document.getElementById('tgConnectForm').style.display = 'block';
    document.getElementById('tgStatus').textContent = 'Not connected';
    document.getElementById('tgStatus').style.color = 'var(--gray-text)';
    document.getElementById('msgStatus').textContent = 'Link Telegram, Discord…';
  } catch(e) { alert(e.message); }
}
