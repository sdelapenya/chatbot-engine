(function () {
  'use strict';

  // ── Configuración desde el tag <script> ─────────────────────────────────
  const script  = document.currentScript;
  const API_URL = (script.getAttribute('data-url') || '').replace(/\/$/, '');
  const COLOR   = script.getAttribute('data-color')   || '#2563eb';
  const TITLE   = script.getAttribute('data-title')   || '¿En qué puedo ayudarte?';
  const BOT     = script.getAttribute('data-bot-name')|| 'Asistente';
  const WELCOME = script.getAttribute('data-welcome') || '';
  const POS     = script.getAttribute('data-position')|| 'right'; // 'left' | 'right'
  const HIDE_POWERED = script.hasAttribute('data-hide-powered-by');

  if (!API_URL) { console.error('[Chatbot Widget] Falta data-url en el tag <script>.'); return; }

  let config = {
    bot: BOT, title: TITLE, welcome: WELCOME,
    business_hours: '', privacy_url: '', powered_by: 'Asistente IA',
  };

  // ── Session ID ───────────────────────────────────────────────────────────
  const SESSION_KEY = '_cw_sid_' + btoa(API_URL).replace(/=/g, '');
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = 'w_' + Math.random().toString(36).slice(2, 11) + '_' + Date.now();
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }

  // ── Estado ───────────────────────────────────────────────────────────────
  let messages    = [];   // {role, content}
  let isOpen      = false;
  let isTyping    = false;
  let leadDetected = false;
  let leadSent     = false;
  let finalized   = false;
  let unread      = 0;

  // ── Colores derivados ────────────────────────────────────────────────────
  function hexToRgb(hex) {
    const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return r + ',' + g + ',' + b;
  }
  const COLOR_RGB  = hexToRgb(COLOR);
  const COLOR_DARK = shadeColor(COLOR, -20);
  function shadeColor(col, pct) {
    const num = parseInt(col.slice(1), 16);
    const amt = Math.round(2.55 * pct);
    const R = Math.min(255, Math.max(0, (num >> 16) + amt));
    const G = Math.min(255, Math.max(0, ((num >> 8) & 0x00FF) + amt));
    const B = Math.min(255, Math.max(0, (num & 0x0000FF) + amt));
    return '#' + (0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1);
  }

  // ── Shadow DOM ───────────────────────────────────────────────────────────
  const host = document.createElement('div');
  host.id = '__chatbot_widget__';
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  // ── CSS ──────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :host { --c: ${COLOR}; --cd: ${COLOR_DARK}; --cr: ${COLOR_RGB}; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }

    /* ── Botón flotante ── */
    #btn {
      position: fixed; bottom: 24px; ${POS}: 24px; z-index: 2147483646;
      width: 56px; height: 56px; border-radius: 50%;
      background: var(--c); color: #fff; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 20px rgba(var(--cr),.45);
      transition: transform .2s, box-shadow .2s;
    }
    #btn:hover { transform: scale(1.08); box-shadow: 0 6px 24px rgba(var(--cr),.55); }
    #btn svg { width: 26px; height: 26px; transition: opacity .2s, transform .2s; }
    #btn .ico-open  { position: absolute; }
    #btn .ico-close { position: absolute; opacity: 0; transform: rotate(-90deg); }
    #btn.open .ico-open  { opacity: 0; transform: rotate(90deg); }
    #btn.open .ico-close { opacity: 1; transform: rotate(0deg); }

    /* Badge de mensajes no leídos */
    #badge {
      position: absolute; top: -4px; ${POS}: -4px;
      background: #ef4444; color: #fff; border-radius: 50%;
      width: 20px; height: 20px; font-size: 11px; font-weight: 700;
      display: none; align-items: center; justify-content: center;
    }
    #badge.show { display: flex; }

    /* ── Ventana de chat ── */
    #window {
      position: fixed; bottom: 92px; ${POS}: 24px; z-index: 2147483645;
      width: 360px; height: 540px; max-height: calc(100vh - 120px);
      background: #fff; border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,.18);
      display: flex; flex-direction: column; overflow: hidden;
      transform: scale(.92) translateY(12px); opacity: 0; pointer-events: none;
      transition: transform .22s cubic-bezier(.34,1.56,.64,1), opacity .18s ease;
    }
    #window.open { transform: scale(1) translateY(0); opacity: 1; pointer-events: all; }

    /* Header */
    #header {
      background: var(--c); color: #fff;
      padding: 14px 16px; display: flex; align-items: center; gap: 10px;
      flex-shrink: 0;
    }
    #avatar {
      width: 36px; height: 36px; border-radius: 50%;
      background: rgba(255,255,255,.25);
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
    }
    #header-text { flex: 1; min-width: 0; }
    #header-title { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #header-sub   { font-size: 11px; opacity: .85; display: flex; align-items: center; gap: 6px; }
    #status-dot {
      width: 7px; height: 7px; border-radius: 50%; background: #4ade80;
      box-shadow: 0 0 0 2px rgba(74,222,128,.35); flex-shrink: 0;
    }

    /* Mensajes */
    #messages {
      flex: 1; overflow-y: auto; padding: 16px;
      display: flex; flex-direction: column; gap: 10px;
      scroll-behavior: smooth;
    }
    #messages::-webkit-scrollbar { width: 4px; }
    #messages::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 2px; }

    .msg { display: flex; flex-direction: column; max-width: 82%; }
    .msg.user   { align-self: flex-end; align-items: flex-end; }
    .msg.bot    { align-self: flex-start; align-items: flex-start; }

    .bubble {
      padding: 10px 13px; border-radius: 16px; font-size: 13.5px; line-height: 1.5;
      word-break: break-word; white-space: pre-wrap;
    }
    .msg.user .bubble { background: var(--c); color: #fff; border-bottom-right-radius: 4px; }
    .msg.bot  .bubble { background: #f3f4f6; color: #111; border-bottom-left-radius: 4px; }

    .msg-time { font-size: 10px; color: #9ca3af; margin-top: 3px; padding: 0 2px; }

    /* Typing indicator */
    .typing-dots {
      display: flex; align-items: center; gap: 4px; padding: 12px 14px;
      background: #f3f4f6; border-radius: 16px; border-bottom-left-radius: 4px;
    }
    .dot {
      width: 7px; height: 7px; border-radius: 50%; background: #9ca3af;
      animation: bounce .9s infinite;
    }
    .dot:nth-child(2) { animation-delay: .15s; }
    .dot:nth-child(3) { animation-delay: .3s; }
    @keyframes bounce { 0%,60%,100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }

    /* Banner lead */
    #lead-banner {
      margin: 0 12px 0; padding: 10px 12px; border-radius: 10px;
      background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534;
      font-size: 12.5px; display: none; align-items: center; gap: 8px;
      flex-shrink: 0;
    }
    #lead-banner.show { display: flex; }
    #lead-banner button {
      margin-left: auto; background: #16a34a; color: #fff; border: none;
      border-radius: 6px; padding: 5px 10px; font-size: 12px; cursor: pointer;
      white-space: nowrap; flex-shrink: 0;
    }
    #lead-banner button:hover { background: #15803d; }

    /* Input */
    #input-area {
      padding: 12px; border-top: 1px solid #e5e7eb;
      display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0;
    }
    #input {
      flex: 1; border: 1.5px solid #e5e7eb; border-radius: 10px;
      padding: 9px 12px; font-size: 13.5px; resize: none; outline: none;
      font-family: inherit; max-height: 100px; line-height: 1.4;
      transition: border-color .15s;
    }
    #input:focus { border-color: var(--c); }
    #send {
      width: 38px; height: 38px; border-radius: 9px; background: var(--c);
      border: none; cursor: pointer; display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: background .15s;
    }
    #send:hover { background: var(--cd); }
    #send:disabled { background: #d1d5db; cursor: not-allowed; }
    #send svg { width: 17px; height: 17px; }

    /* Powered by / privacidad */
    #footer-meta {
      text-align: center; padding: 6px 10px 8px; font-size: 10px; color: #9ca3af;
      flex-shrink: 0; line-height: 1.4;
    }
    #footer-meta a { color: #6b7280; text-decoration: underline; }
    #footer-meta.hidden { display: none; }

    @media (max-width: 420px) {
      #window { width: calc(100vw - 16px); ${POS}: 8px; bottom: 80px; }
      #btn    { bottom: 16px; ${POS}: 12px; }
    }
  `;
  shadow.appendChild(style);

  // ── HTML ─────────────────────────────────────────────────────────────────
  const root = document.createElement('div');
  root.innerHTML = `
    <button id="btn" aria-label="Chat">
      <div id="badge"></div>
      <svg class="ico-open"  fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h.01M12 10h.01M16 10h.01M21 12c0 4.418-4.03 8-9 8a9.77 9.77 0 01-4.44-1.053L3 21l1.672-4.33A7.966 7.966 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
      <svg class="ico-close" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
    </button>

    <div id="window" role="dialog" aria-label="Chat" aria-modal="true">
      <div id="header">
        <div id="avatar">
          <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="white" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15M14.25 3.104c.251.023.501.05.75.082M19.8 15l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.607L4 15m15.8 0l1.402 1.402c1 1 .03 2.7-1.388 2.43l-4.552-.91a9 9 0 00-3.524 0l-4.552.91c-1.418.27-2.389-1.43-1.388-2.43L4 15"/></svg>
        </div>
        <div id="header-text">
          <div id="header-title">${escHtml(BOT)}</div>
          <div id="header-sub"><span id="status-dot"></span><span id="header-status">En línea</span></div>
        </div>
      </div>

      <div id="messages"></div>

      <div id="lead-banner">
        <span id="lead-banner-text">Listo: pulsa para enviar tu consulta al equipo comercial.</span>
        <button id="finalize-btn">Enviar consulta</button>
      </div>

      <div id="input-area">
        <textarea id="input" rows="1" placeholder="Escribe tu mensaje..." aria-label="Mensaje"></textarea>
        <button id="send" aria-label="Enviar">
          <svg fill="none" viewBox="0 0 24 24" stroke="white" stroke-width="2.2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5"/></svg>
        </button>
      </div>
      <div id="footer-meta"></div>
    </div>
  `;
  shadow.appendChild(root);

  // ── Referencias ──────────────────────────────────────────────────────────
  const $btn      = shadow.getElementById('btn');
  const $badge    = shadow.getElementById('badge');
  const $window   = shadow.getElementById('window');
  const $msgs     = shadow.getElementById('messages');
  const $input    = shadow.getElementById('input');
  const $send     = shadow.getElementById('send');
  const $banner   = shadow.getElementById('lead-banner');
  const $finalize = shadow.getElementById('finalize-btn');

  // ── Helpers ──────────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function formatMessage(text) {
    let s = escHtml(text);
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }
  function applyConfig() {
    shadow.getElementById('header-title').textContent = config.bot || BOT;
    const sub = shadow.getElementById('header-status');
    if (sub) sub.textContent = config.title || TITLE;
    const footer = shadow.getElementById('footer-meta');
    if (!footer) return;
    if (HIDE_POWERED) { footer.classList.add('hidden'); return; }
    let html = escHtml(config.powered_by || 'Asistente IA');
    if (config.privacy_url) {
      html += ' · <a href="' + escHtml(config.privacy_url) + '" target="_blank" rel="noopener">Privacidad</a>';
    }
    footer.innerHTML = html;
    footer.classList.remove('hidden');
  }
  async function loadConfig() {
    try {
      const res = await fetch(API_URL + '/api/config');
      if (res.ok) Object.assign(config, await res.json());
      applyConfig();
    } catch (_) { applyConfig(); }
  }
  function now() {
    return new Date().toLocaleTimeString('es-ES', {hour:'2-digit', minute:'2-digit'});
  }
  function scrollBottom() {
    $msgs.scrollTop = $msgs.scrollHeight;
  }

  // ── Renderizar mensaje ───────────────────────────────────────────────────
  function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
    const body = role === 'user' ? escHtml(content) : formatMessage(content);
    div.innerHTML = '<div class="bubble">' + body + '</div>'
                  + '<span class="msg-time">' + now() + '</span>';
    $msgs.appendChild(div);
    scrollBottom();
    if (!isOpen && role !== 'user') {
      unread++;
      $badge.textContent = unread > 9 ? '9+' : unread;
      $badge.classList.add('show');
    }
  }

  function showTyping() {
    if (isTyping) return;
    isTyping = true;
    const div = document.createElement('div');
    div.className = 'msg bot'; div.id = '_typing';
    div.innerHTML = '<div class="typing-dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
    $msgs.appendChild(div);
    scrollBottom();
  }
  function hideTyping() {
    const el = shadow.getElementById('_typing');
    if (el) el.remove();
    isTyping = false;
  }

  // ── Abrir/cerrar ─────────────────────────────────────────────────────────
  function openChat() {
    isOpen = true;
    $window.classList.add('open');
    $btn.classList.add('open');
    unread = 0;
    $badge.classList.remove('show');
    $input.focus();
    scrollBottom();
  }
  function closeChat() {
    isOpen = false;
    $window.classList.remove('open');
    $btn.classList.remove('open');
  }

  function updateLeadBanner(data) {
    if (!data.lead_ready) return;
    leadDetected = true;
    $banner.classList.add('show');
    const $text = shadow.getElementById('lead-banner-text');
    if (data.lead_sent || data.lead_auto_sent) {
      leadSent = true;
      if ($text) $text.textContent = '✓ Consulta registrada. ¿Reenviar al equipo comercial?';
      $finalize.textContent = 'Reenviar consulta';
    } else {
      if ($text) $text.textContent = 'Listo: pulsa para enviar tu consulta al equipo.';
      $finalize.textContent = 'Enviar consulta';
    }
  }
  $btn.addEventListener('click', () => isOpen ? closeChat() : openChat());

  // ── Enviar mensaje ───────────────────────────────────────────────────────
  async function sendMessage() {
    const text = $input.value.trim();
    if (!text || isTyping || finalized) return;
    $input.value = ''; autoResize();
    messages.push({role:'user', content:text});
    addMessage('user', text);
    $send.disabled = true;
    showTyping();

    try {
      const res = await fetch(API_URL + '/api/chat', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({session_id: sessionId, messages})
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      hideTyping();
      messages.push({role:'assistant', content:data.reply});
      addMessage('bot', data.reply);
      updateLeadBanner(data);
    } catch (e) {
      hideTyping();
      addMessage('bot', 'Lo siento, ha ocurrido un error. Por favor, inténtalo de nuevo.');
    } finally {
      $send.disabled = false;
      $input.focus();
    }
  }

  // ── Finalizar / enviar lead ──────────────────────────────────────────────
  async function doFinalize() {
    if (finalized) return;
    finalized = true;
    $banner.classList.remove('show');
    $finalize.disabled = true;
    $finalize.textContent = 'Enviando…';
    try {
      const res = await fetch(API_URL + '/api/finalize', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({session_id: sessionId, resend: leadSent})
      });
      const data = res.ok ? await res.json() : {};
      if (data.email_sent === false) {
        addMessage('bot', 'Hemos guardado tu conversación. Si puedes, deja un email o teléfono para que te contactemos.');
        finalized = false;
        $finalize.disabled = false;
        $finalize.textContent = 'Enviar consulta';
        $banner.classList.add('show');
        return;
      }
    } catch (_) {}
    leadSent = true;
    $finalize.textContent = 'Enviado ✓';
  }

  $finalize.addEventListener('click', async () => {
    await doFinalize();
    if (!finalized) return;
    if (!leadSent) {
      addMessage('bot', 'Consulta enviada. Nuestro equipo comercial te contactará pronto. ¡Gracias!');
    }
    $input.disabled = true;
    $send.disabled  = true;
    $input.placeholder = 'Consulta enviada';
  });

  // ── Input adaptable y envío con Enter ────────────────────────────────────
  function autoResize() {
    $input.style.height = 'auto';
    $input.style.height = Math.min($input.scrollHeight, 100) + 'px';
  }
  $input.addEventListener('input', autoResize);
  $input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  $send.addEventListener('click', sendMessage);

  // ── Cerrar con Escape ────────────────────────────────────────────────────
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen) closeChat(); });

  loadConfig().then(() => {
    const welcome = config.welcome || ('¡Hola! Soy ' + (config.bot || BOT) + '. ¿En qué puedo ayudarte?');
    setTimeout(() => {
      addMessage('bot', welcome);
      if (config.business_hours) {
        setTimeout(() => addMessage('bot', config.business_hours), 400);
      }
    }, 500);
  });

})();
