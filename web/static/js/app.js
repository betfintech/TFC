/**
 * app.js — SPA router, navigation, page loader
 */

const App = {
  currentPage: null,
  statusInterval: null,

  pages: {
    dashboard:        { title:'Dashboard',         icon:'🏠' },
    signals:          { title:'Signals',           icon:'📊' },
    chart:            { title:'Chart',             icon:'📈' },
    'risk-calculator':{ title:'Risk Calculator',   icon:'💰' },
    chat:             { title:'Chat',              icon:'💬' },
    account:          { title:'Account',           icon:'👤' },
  },

  init() {
    this.bindNav();
    this.bindMobileMenu();

    // Route from hash or URL
    const hash = window.location.hash.slice(1);
    this.navigate(hash && this.pages[hash] ? hash : 'dashboard', false);

    // Update footer clock
    this.startStatusPoll();
    setInterval(() => {
      const el = Utils.qs('#footer-time');
      if (el) el.textContent = Utils.fmt.utcNow();
    }, 1000);

    window.addEventListener('popstate', () => {
      const hash = window.location.hash.slice(1);
      if (hash && this.pages[hash]) this.navigate(hash, false);
    });
  },

  navigate(page, pushState=true) {
    if (!this.pages[page]) page = 'dashboard';
    if (this.currentPage === page) return;

    SignalsFeed.stopAutoRefresh?.();

    const main = Utils.qs('#app-main');
    if (main) {
      main.style.opacity = '0';
      setTimeout(() => {
        main.innerHTML = this.getPageContent(page);
        main.style.opacity = '1';
        main.classList.add('animate-in');
        this.initPage(page);
      }, 120);
    }

    this.currentPage = page;
    this.updateNavActive(page);
    if (pushState) window.history.pushState({ page }, '', `#${page}`);

    // Update document title
    document.title = `${this.pages[page]?.title || 'Dashboard'} — Trading Signal System`;
  },

  getPageContent(page) {
    switch(page) {
      case 'dashboard':         return this.pageDashboard();
      case 'signals':           return this.pageSignals();
      case 'chart':             return this.pageChart();
      case 'risk-calculator':   return this.pageRiskCalc();
      case 'chat':              return this.pageChat();
      case 'account':           return this.pageAccount();
      default:                  return '<p>Page not found</p>';
    }
  },

  initPage(page) {
    switch(page) {
      case 'dashboard':       this.initDashboard();  break;
      case 'signals':         initSignals();         break;
      case 'chart':           initChart();           break;
      case 'risk-calculator': initRiskCalculator();  break;
      case 'chat':            this.initChat();       break;
      case 'account':         this.initAccount();    break;
    }
  },

  // ── Page Templates ──

  pageDashboard() {
    return `
      <div class="page-header">
        <h1 class="page-title">Dashboard</h1>
        <p class="page-sub">Live overview of your trading signal system</p>
      </div>
      <div class="grid-4" id="dash-stats" style="margin-bottom:20px">
        ${['Status','Active Pairs','Today Signals','Last Signal'].map(l => `
          <div class="stat-card">
            <div class="stat-label">${l}</div>
            <div class="stat-value" style="font-size:1rem;padding-top:4px">
              <div class="spinner" style="width:16px;height:16px;border-width:2px"></div>
            </div>
          </div>
        `).join('')}
      </div>
      <div class="grid-2" style="gap:16px;margin-bottom:16px">
        <div class="card">
          <div class="card-header">
            <span class="card-title">Recent Signals</span>
            <a href="#" onclick="App.navigate('signals')" style="font-size:0.78rem">View all →</a>
          </div>
          <div id="dash-signals">
            <div style="display:flex;justify-content:center;padding:20px">
              <div class="spinner"></div>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-header"><span class="card-title">Market Heatmap</span></div>
          <div id="dash-heatmap" class="grid-3" style="gap:6px"></div>
        </div>
      </div>
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="App.navigate('risk-calculator')">💰 Risk Calculator</button>
        <button class="btn btn-secondary" onclick="App.navigate('signals')">📊 All Signals</button>
        <button class="btn btn-secondary" onclick="App.navigate('chart')">📈 Chart Viewer</button>
      </div>
    `;
  },

  pageSignals() {
    return `
      <div class="page-header">
        <h1 class="page-title">Signals Feed</h1>
        <p class="page-sub">Live trading signals — auto-refreshes every 5s</p>
      </div>
      <div id="signals-root"></div>
    `;
  },

  pageChart() {
    return `
      <div class="page-header">
        <h1 class="page-title">Chart Viewer</h1>
        <p class="page-sub">Technical analysis — real-time OHLC data</p>
      </div>
      <div id="chart-root"></div>
    `;
  },

  pageRiskCalc() {
    return `
      <div class="page-header">
        <h1 class="page-title">Risk Management Calculator</h1>
        <p class="page-sub">Position sizing, multi-TP setup, and scenario analysis</p>
      </div>
      <div class="card" style="padding:10px 14px;margin-bottom:16px;border-color:var(--warning);background:rgba(210,153,34,0.07)">
        <span style="font-size:0.8rem;color:var(--warning)">⚠️ <strong>Disclaimer:</strong> All calculations are estimates for educational purposes only. Not financial advice.</span>
      </div>
      <div id="calc-root"></div>
    `;
  },

  pageChat() {
    return `
      <div class="page-header">
        <h1 class="page-title">Chat with Bot</h1>
        <p class="page-sub">Ask about signals, risk management, or market analysis</p>
      </div>
      <div class="card" style="display:flex;flex-direction:column;height:520px">
        <div id="chat-messages" style="flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px">
          <div class="chat-msg bot">
            <div class="chat-bubble">👋 Hi! I can help with signals, risk calculations, and market analysis.</div>
            <div class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap">
              ${['How to use Risk Calc?','Show recent signals','What is EUR/USD signal?'].map(q => `
                <button class="btn btn-sm btn-secondary" onclick="App.sendChatMsg('${q}')">${q}</button>
              `).join('')}
            </div>
          </div>
        </div>
        <div class="row" style="gap:8px;padding:12px;border-top:1px solid var(--border-primary)">
          <input class="input" id="chat-input" placeholder="Type a message…" style="flex:1">
          <button class="btn btn-primary" id="chat-send">Send</button>
        </div>
      </div>
    `;
  },

  pageAccount() {
    return `
      <div class="page-header">
        <h1 class="page-title">My Account</h1>
        <p class="page-sub">Profile, subscription, and preferences</p>
      </div>
      <div class="grid-2" style="gap:16px">
        <div class="stack">
          <div class="card">
            <div class="card-header"><span class="card-title">Profile</span></div>
            <div class="stack">
              <div class="result-item"><div class="label">Username</div><div class="value" style="font-size:0.9rem" id="acc-username">Loading…</div></div>
              <div class="result-item"><div class="label">Plan</div><div class="value green" style="font-size:0.9rem" id="acc-plan">—</div></div>
              <div class="result-item"><div class="label">Status</div><div class="value green" style="font-size:0.9rem" id="acc-status">—</div></div>
            </div>
          </div>
          <div class="card">
            <div class="card-header"><span class="card-title">Default Settings</span></div>
            <div class="stack">
              <div class="input-group">
                <label>Default Risk %</label>
                <div class="input-prefix"><span>%</span><input class="input" id="pref-risk" type="number" value="${Utils.storage.get('pref_risk',2)}" step="0.5" min="0.1" max="10"></div>
              </div>
              <div class="input-group">
                <label>Chart Timeframe</label>
                <select class="input select" id="pref-tf">
                  <option>M1</option><option>M5</option><option>M15</option>
                  <option selected>H1</option><option>D1</option>
                </select>
              </div>
              <button class="btn btn-primary" id="pref-save-btn" style="width:100%">💾 Save Preferences</button>
            </div>
          </div>
        </div>
        <div class="stack">
          <div class="card">
            <div class="card-header"><span class="card-title">Notifications</span></div>
            <div class="stack">
              ${[
                ['email_signal','Email on New Signal',true],
                ['email_weekly','Weekly Summary',true],
                ['browser_notif','Browser Notifications',false],
              ].map(([k,l,def]) => `
                <label class="toggle">
                  <input type="checkbox" id="${k}" ${Utils.storage.get('notif_'+k, def)?'checked':''}
                    onchange="Utils.storage.set('notif_${k}', this.checked); Utils.toast('Preference saved','success')">
                  <div class="toggle-track"></div>
                  <span style="font-size:0.875rem">${l}</span>
                </label>
              `).join('')}
            </div>
          </div>
          <div class="card">
            <div class="card-header"><span class="card-title">Data</span></div>
            <div class="stack">
              <button class="btn btn-secondary" onclick="App.exportData()">📥 Export My Data (JSON)</button>
              <button class="btn btn-danger" onclick="if(confirm('Clear all local data?')) { localStorage.clear(); Utils.toast('Data cleared','success'); }">🗑️ Clear Local Data</button>
            </div>
          </div>
        </div>
      </div>
    `;
  },

  // ── Page Initializers ──

  async initDashboard() {
    const r = await API.status();
    const stats = r.ok ? r.data : {};

    // FIX: backend returns `engine_status` ("running"/"stopped"/"starting"),
    // not a boolean `running` field. Map it correctly here.
    const isRunning = stats.engine_status === 'running';

    // FIX: backend returns `pairs_scanned_last_cycle`, `signals_sent_today`,
    // and nested `last_signal.time` — not `active_pairs`, `signals_today`, `last_signal_at`.
    const activePairs  = stats.pairs_scanned_last_cycle ?? '—';
    const signalsToday = stats.signals_sent_today       ?? '—';
    const lastSignalAt = stats.last_signal?.time        ?? null;

    const statsEl = Utils.qs('#dash-stats');
    if (statsEl) {
      statsEl.innerHTML = [
        { label:'Status',        value: isRunning ? '🟢 Online' : '🔴 Offline', cls: isRunning ? 'green' : 'red' },
        { label:'Active Pairs',  value: activePairs },
        { label:'Today Signals', value: signalsToday },
        { label:'Last Signal',   value: lastSignalAt ? Utils.fmt.timeAgo(lastSignalAt) : '—' },
      ].map(s => `
        <div class="stat-card">
          <div class="stat-label">${s.label}</div>
          <div class="stat-value ${s.cls||''}" style="font-size:1rem">${s.value}</div>
        </div>
      `).join('');
    }

    const sr = await API.signals(5);
    const sigEl = Utils.qs('#dash-signals');
    if (sigEl) {
      if (sr.ok && sr.data?.length) {
        sigEl.innerHTML = sr.data.slice(0,5).map(s => {
          const dir = (s.direction||s.type||'').toUpperCase();
          return `
            <div class="signal-row" style="cursor:default">
              <span class="badge badge-${dir==='BUY'?'buy':'sell'}">${dir}</span>
              <div>
                <div class="signal-pair">${s.symbol||s.pair||'—'}</div>
                <div class="signal-meta">Entry: ${s.entry||'—'} | SL: ${s.stop_loss||'—'} | TP1: ${s.tp1||'—'}</div>
              </div>
              <div class="signal-meta">${s.timestamp?Utils.fmt.timeAgo(s.timestamp):''}</div>
            </div>
          `;
        }).join('');
      } else {
        sigEl.innerHTML = '<p style="color:var(--text-tertiary);padding:12px;font-size:0.85rem">No recent signals.</p>';
      }
    }

    // Heatmap
    const hmEl = Utils.qs('#dash-heatmap');
    if (hmEl) {
      const pairs = ['EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CAD','BTC/USD'];
      const cls   = ['bull','bear','neut','bull','bull','bear'];
      hmEl.innerHTML = pairs.map((p,i) => `
        <div class="heatmap-cell ${cls[i]}">${p.replace('/USD','')}</div>
      `).join('');
    }
  },

  initChat() {
    const input = Utils.qs('#chat-input');
    const send  = Utils.qs('#chat-send');
    send?.addEventListener('click', () => {
      const msg = input?.value?.trim();
      if (msg) { this.sendChatMsg(msg); if(input) input.value = ''; }
    });
    input?.addEventListener('keypress', e => {
      if (e.key === 'Enter') send?.click();
    });
  },

  async sendChatMsg(msg) {
    const box = Utils.qs('#chat-messages');
    if (!box) return;
    box.innerHTML += `<div class="chat-msg user"><div class="chat-bubble user-bubble">${msg}</div></div>`;
    box.scrollTop = box.scrollHeight;

    const typingEl = document.createElement('div');
    typingEl.className = 'chat-msg bot';
    typingEl.innerHTML = '<div class="chat-bubble"><span class="spinner" style="width:12px;height:12px;display:inline-block"></span></div>';
    box.appendChild(typingEl);
    box.scrollTop = box.scrollHeight;

    const r = await API.chat(msg);
    typingEl.remove();
    const reply = r.data?.reply || 'Sorry, I could not process that.';
    box.innerHTML += `<div class="chat-msg bot"><div class="chat-bubble">${reply}</div></div>`;
    box.scrollTop = box.scrollHeight;
  },

  async initAccount() {
    const r = await API.profile();
    if (r.ok && r.data) {
      const d = r.data;
      const set = (id, v) => { const el = Utils.qs(id); if(el) el.textContent = v; };
      set('#acc-username', d.username || 'trader');
      set('#acc-plan',     d.plan || 'Free');
      set('#acc-status',   d.subscription_status || 'active');
    }
    Utils.qs('#pref-save-btn')?.addEventListener('click', () => {
      Utils.storage.set('pref_risk', parseFloat(Utils.qs('#pref-risk')?.value));
      Utils.toast('Preferences saved', 'success');
    });
  },

  exportData() {
    const data = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      data[k] = Utils.storage.get(k);
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'trading-data.json';
    a.click();
    Utils.toast('Data exported', 'success');
  },

  // ── Helpers ──

  updateNavActive(page) {
    Utils.qsa('[data-page]').forEach(link => {
      link.classList.toggle('active', link.dataset.page === page);
    });
  },

  bindNav() {
    Utils.qsa('[data-page]').forEach(link => {
      link.addEventListener('click', e => {
        e.preventDefault();
        const page = link.dataset.page;
        this.navigate(page);
        this.closeMobileMenu();
      });
    });
  },

  bindMobileMenu() {
    const ham  = Utils.qs('#hamburger');
    const side = Utils.qs('.sidebar');
    ham?.addEventListener('click', () => side?.classList.toggle('open'));
  },

  closeMobileMenu() {
    Utils.qs('.sidebar')?.classList.remove('open');
  },

  startStatusPoll() {
    // Run once immediately, then repeat every 30s
    const poll = async () => {
      const r = await API.status();
      const dot = Utils.qs('.status-dot');
      const txt = Utils.qs('#footer-status-text');
      // FIX: check engine_status field, not r.data.ok (which is always true on success)
      const isRunning = r.ok && r.data?.engine_status === 'running';
      if (isRunning) {
        if (dot) dot.style.background = 'var(--success)';
        if (txt) txt.textContent = 'Online';
      } else {
        if (dot) dot.style.background = 'var(--danger)';
        if (txt) txt.textContent = 'Offline';
      }
    };
    poll();
    this.statusInterval = setInterval(poll, 30000);
  },
};

// Chat CSS injected here to keep styles centralized
const chatCSS = `
.chat-msg      { display:flex; flex-direction:column; max-width:85%; }
.chat-msg.user { align-self:flex-end; align-items:flex-end; }
.chat-msg.bot  { align-self:flex-start; }
.chat-bubble   { background:var(--bg-tertiary); border-radius:12px; padding:10px 14px; font-size:0.875rem; color:var(--text-primary); border:1px solid var(--border-secondary); }
.user-bubble   { background:var(--active); border-color:var(--active); }
`;
const styleEl = document.createElement('style');
styleEl.textContent = chatCSS;
document.head.appendChild(styleEl);

document.addEventListener('DOMContentLoaded', () => App.init());
window.App = App;
