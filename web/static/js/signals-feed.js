/**
 * signals-feed.js — Signal list rendering & filtering
 */

const SignalsFeed = {
  signals: [],
  filtered: [],
  autoRefreshTimer: null,
  REFRESH_INTERVAL: 5000,
  currentPage: 1,
  perPage: 10,
  filter: { direction:'all', search:'' },

  async init() {
    await this.load();
    this.bindFilters();
    this.startAutoRefresh();
  },

  async load() {
    const r = await API.signals(100);
    if (r.ok && Array.isArray(r.data)) {
      this.signals = r.data;
      this.applyFilter();
    }
    this.render();
  },

  applyFilter() {
    let s = [...this.signals];
    if (this.filter.direction !== 'all') {
      s = s.filter(sig => (sig.direction||sig.type||'').toUpperCase() === this.filter.direction);
    }
    if (this.filter.search) {
      const q = this.filter.search.toLowerCase();
      s = s.filter(sig => (sig.symbol||sig.pair||'').toLowerCase().includes(q));
    }
    this.filtered = s;
    this.currentPage = 1;
  },

  render() {
    const el = Utils.qs('#signals-root');
    if (!el) return;
    const start = (this.currentPage-1)*this.perPage;
    const page  = this.filtered.slice(start, start+this.perPage);
    const total = Math.ceil(this.filtered.length/this.perPage);

    el.innerHTML = `
      <div class="card" style="margin-bottom:16px;padding:12px">
        <div class="row" style="gap:10px;flex-wrap:wrap">
          <input class="input" id="sig-search" placeholder="🔍 Search symbol..." style="flex:1;min-width:140px" value="${this.filter.search}">
          <select class="input select" id="sig-dir" style="width:130px">
            <option value="all">All Directions</option>
            <option value="BUY" ${this.filter.direction==='BUY'?'selected':''}>BUY only</option>
            <option value="SELL" ${this.filter.direction==='SELL'?'selected':''}>SELL only</option>
          </select>
          <div style="font-size:0.8rem;color:var(--text-tertiary);align-self:center">${this.filtered.length} signals</div>
        </div>
      </div>

      <div class="card" style="padding:0;overflow:hidden">
        ${page.length ? page.map(s => this.renderRow(s)).join('') : `
          <div style="padding:32px;text-align:center;color:var(--text-tertiary)">
            <div style="font-size:2rem;margin-bottom:8px">📭</div>
            <p>No signals found</p>
          </div>
        `}
      </div>

      <div class="row" style="margin-top:16px;justify-content:center;gap:8px">
        <button class="btn btn-secondary btn-sm" id="sig-prev" ${this.currentPage<=1?'disabled':''}>← Prev</button>
        <span style="font-size:0.8rem;color:var(--text-secondary)">Page ${this.currentPage} / ${total||1}</span>
        <button class="btn btn-secondary btn-sm" id="sig-next" ${this.currentPage>=total?'disabled':''}>Next →</button>
      </div>
    `;

    this.bindPagination(total);
    this.bindFilters();
    this.bindRows();
  },

  renderRow(s) {
    const pair = s.symbol || s.pair || 'N/A';
    const dir  = (s.direction || s.type || '').toUpperCase();
    const entry = s.entry || s.entry_price || '—';
    const sl    = s.stop_loss || s.sl || '—';
    const tp1   = s.tp1 || s.take_profit_1 || '—';
    const ts    = s.timestamp || s.created_at || '';
    const rr    = s.risk_reward || '—';

    return `
      <div class="signal-row" data-signal='${JSON.stringify(s).replace(/'/g,"&#39;")}'>
        <span class="badge badge-${dir==='BUY'?'buy':dir==='SELL'?'sell':'hold'}">${dir||'—'}</span>
        <div>
          <div class="signal-pair">${pair}</div>
          <div class="signal-meta">Entry: ${entry} | SL: ${sl} | TP1: ${tp1} ${ts?'· '+Utils.fmt.timeAgo(ts):''}</div>
        </div>
        <div class="signal-price">${entry}</div>
        <div class="signal-rr" style="color:var(--info)">${rr !== '—' ? `R:R ${rr}` : ''}</div>
      </div>
    `;
  },

  bindRows() {
    Utils.qsa('.signal-row').forEach(row => {
      row.addEventListener('click', () => {
        try {
          const s = JSON.parse(row.dataset.signal);
          this.showModal(s);
        } catch {}
      });
    });
  },

  showModal(s) {
    const pair = s.symbol || s.pair || 'N/A';
    const dir  = (s.direction || s.type || '').toUpperCase();
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <h3 class="modal-title">${pair} — <span class="badge badge-${dir==='BUY'?'buy':'sell'}">${dir}</span></h3>
          <button class="modal-close">✕</button>
        </div>
        <div class="stack">
          <div class="card" style="padding:12px">
            <div class="grid-2" style="gap:8px">
              ${[
                ['Entry',   s.entry || s.entry_price || '—'],
                ['Stop Loss', s.stop_loss || s.sl || '—'],
                ['TP1', s.tp1 || '—'],
                ['TP2', s.tp2 || '—'],
                ['TP3', s.tp3 || '—'],
                ['R:R', s.risk_reward || '—'],
              ].map(([l,v]) => `
                <div class="result-item">
                  <div class="label">${l}</div>
                  <div class="value">${v}</div>
                </div>
              `).join('')}
            </div>
          </div>
          ${s.timestamp ? `<p style="font-size:0.78rem;color:var(--text-tertiary)">📅 ${new Date(s.timestamp).toUTCString()}</p>` : ''}
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <button class="btn btn-secondary" onclick="Utils.copy('${pair} ${dir} Entry:${s.entry||''} SL:${s.stop_loss||''} TP1:${s.tp1||''}')">📋 Copy</button>
            <button class="btn btn-primary" onclick="document.querySelector('.modal-overlay').remove();App.navigate('risk-calculator')">💰 Risk Calc</button>
          </div>
        </div>
      </div>
    `;
    overlay.querySelector('.modal-close').onclick = () => overlay.remove();
    overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  },

  bindFilters() {
    const search = Utils.qs('#sig-search');
    const dir    = Utils.qs('#sig-dir');
    if (search) search.addEventListener('input', Utils.debounce(e => {
      this.filter.search = e.target.value;
      this.applyFilter(); this.render();
    }));
    if (dir) dir.addEventListener('change', e => {
      this.filter.direction = e.target.value;
      this.applyFilter(); this.render();
    });
  },

  bindPagination(total) {
    Utils.qs('#sig-prev')?.addEventListener('click', () => {
      if (this.currentPage > 1) { this.currentPage--; this.render(); }
    });
    Utils.qs('#sig-next')?.addEventListener('click', () => {
      if (this.currentPage < total) { this.currentPage++; this.render(); }
    });
  },

  startAutoRefresh() {
    clearInterval(this.autoRefreshTimer);
    this.autoRefreshTimer = setInterval(() => {
      if (Utils.qs('#signals-root')) this.load();
      else this.stopAutoRefresh();
    }, this.REFRESH_INTERVAL);
  },

  stopAutoRefresh() { clearInterval(this.autoRefreshTimer); },
};

function initSignals() { SignalsFeed.init(); }
window.SignalsFeed = SignalsFeed;
