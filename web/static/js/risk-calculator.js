/**
 * risk-calculator.js — Position sizing & risk management UI
 */

const RiskCalc = {
  activeTab: 'position',
  savedCalcs: Utils.storage.get('risk_calcs', []),

  init() {
    this.render();
    this.loadSaved();
  },

  render() {
    const el = Utils.qs('#calc-root');
    if (!el) return;
    el.innerHTML = `
      <div class="tabs" id="calc-tabs">
        <div class="tab active" data-tab="position">📊 Position Sizer</div>
        <div class="tab" data-tab="multitp">📈 Multi-TP Setup</div>
        <div class="tab" data-tab="scenarios">⚠️ Scenarios</div>
      </div>

      <div id="tab-position" class="tab-panel animate-in">
        ${this.renderPositionSizer()}
      </div>
      <div id="tab-multitp" class="tab-panel" style="display:none">
        ${this.renderMultiTP()}
      </div>
      <div id="tab-scenarios" class="tab-panel" style="display:none">
        ${this.renderScenarios()}
      </div>
    `;
    this.bindTabs();
    this.bindPositionForm();
    this.bindMultiTPForm();
  },

  renderPositionSizer() {
    return `
      <div class="grid-2" style="gap:20px">
        <div class="card">
          <div class="card-header">
            <span class="card-title">Inputs</span>
          </div>
          <div class="stack">
            <div class="input-group">
              <label>Account Balance</label>
              <div class="input-prefix">
                <span>$</span>
                <input class="input" id="ps-balance" type="number" value="10000" min="0" placeholder="10000">
              </div>
              <span class="hint">Your total trading capital in USD</span>
            </div>
            <div class="input-group">
              <label>Risk per Trade (%)</label>
              <div class="input-prefix">
                <span>%</span>
                <input class="input" id="ps-risk" type="number" value="2" min="0.01" max="20" step="0.1" placeholder="2">
              </div>
              <span class="hint">Recommended: 1–3%</span>
            </div>
            <div class="input-group">
              <label>Entry Price</label>
              <input class="input" id="ps-entry" type="number" step="0.00001" placeholder="1.0950">
            </div>
            <div class="input-group">
              <label>Stop Loss Price</label>
              <input class="input" id="ps-sl" type="number" step="0.00001" placeholder="1.0900">
              <span class="hint" id="ps-pips-hint"></span>
            </div>
            <div class="row" style="gap:12px">
              <div class="input-group" style="flex:1">
                <label>Instrument</label>
                <select class="input select" id="ps-instrument">
                  <option value="forex">Forex</option>
                  <option value="crypto">Crypto</option>
                </select>
              </div>
              <div class="input-group" style="flex:1">
                <label>Symbol</label>
                <select class="input select" id="ps-symbol">
                  <option>EUR/USD</option><option>GBP/USD</option><option>USD/JPY</option>
                  <option>AUD/USD</option><option>USD/CHF</option><option>USD/CAD</option>
                </select>
              </div>
            </div>
            <button class="btn btn-primary btn-lg" id="ps-calc-btn" style="width:100%">
              ⚡ Calculate Position Size
            </button>
          </div>
        </div>

        <div>
          <div class="card" id="ps-results" style="display:none">
            <div class="card-header">
              <span class="card-title">✅ Results</span>
              <div class="row" style="gap:6px">
                <button class="btn btn-sm btn-secondary" id="ps-copy-btn">📋 Copy</button>
                <button class="btn btn-sm btn-secondary" id="ps-save-btn">💾 Save</button>
              </div>
            </div>
            <div class="result-grid" id="ps-result-grid"></div>
          </div>

          <div class="card" style="margin-top:16px" id="ps-history-card">
            <div class="card-header">
              <span class="card-title">📌 Saved Setups</span>
              <button class="btn btn-sm btn-danger" id="ps-clear-btn">Clear All</button>
            </div>
            <div id="ps-history-list">
              <p style="color:var(--text-tertiary);font-size:0.8rem;padding:8px 0">No saved setups yet.</p>
            </div>
          </div>
        </div>
      </div>
    `;
  },

  renderMultiTP() {
    return `
      <div class="grid-2" style="gap:20px">
        <div class="card">
          <div class="card-header"><span class="card-title">Position Summary</span></div>
          <div class="stack">
            <div class="row" style="gap:12px">
              <div class="input-group" style="flex:1">
                <label>Entry Price</label>
                <input class="input" id="tp-entry" type="number" step="0.00001" placeholder="1.0950">
              </div>
              <div class="input-group" style="flex:1">
                <label>Stop Loss</label>
                <input class="input" id="tp-sl" type="number" step="0.00001" placeholder="1.0900">
              </div>
            </div>
            <div class="input-group">
              <label>Position Size (lots)</label>
              <input class="input" id="tp-lots" type="number" step="0.01" value="0.20" placeholder="0.20">
            </div>

            <div class="card-header" style="margin-top:8px">
              <span class="card-title">Take Profit Levels</span>
              <button class="btn btn-sm btn-secondary" id="tp-add-btn">+ Add TP</button>
            </div>
            <div id="tp-levels-container" class="stack">
              ${this.renderTPLevel(1, 1.0980, 50)}
              ${this.renderTPLevel(2, 1.1010, 30)}
              ${this.renderTPLevel(3, 1.1050, 20)}
            </div>
            <button class="btn btn-primary btn-lg" id="tp-calc-btn" style="width:100%">
              ⚡ Calculate Breakdown
            </button>
          </div>
        </div>

        <div>
          <div class="card" id="tp-results" style="display:none">
            <div class="card-header"><span class="card-title">📊 Scenario Outcomes</span></div>
            <div id="tp-breakdown-table"></div>
            <hr class="divider">
            <div class="result-grid" id="tp-summary-grid"></div>
          </div>
        </div>
      </div>
    `;
  },

  renderTPLevel(n, price='', qty='') {
    return `
      <div class="card" style="padding:12px" data-tp-level="${n}">
        <div class="row-between" style="margin-bottom:8px">
          <span style="font-size:0.8rem;font-weight:700;color:var(--info)">TP ${n}</span>
          <button class="btn btn-sm btn-danger tp-remove-btn" data-n="${n}">✕</button>
        </div>
        <div class="row" style="gap:10px">
          <div class="input-group" style="flex:2">
            <label>Price</label>
            <input class="input tp-price" type="number" step="0.00001" value="${price}" placeholder="1.0980">
          </div>
          <div class="input-group" style="flex:1">
            <label>Qty %</label>
            <input class="input tp-qty" type="number" min="1" max="100" value="${qty}" placeholder="50">
          </div>
        </div>
      </div>
    `;
  },

  renderScenarios() {
    return `
      <div class="card">
        <div class="card-header"><span class="card-title">Break-Even & Win Rate Analysis</span></div>
        <div class="grid-2" style="gap:20px">
          <div class="stack">
            <div class="input-group">
              <label>Average Win ($)</label>
              <div class="input-prefix"><span>$</span><input class="input" id="sc-win" type="number" value="1060" placeholder="1060"></div>
            </div>
            <div class="input-group">
              <label>Average Loss ($)</label>
              <div class="input-prefix"><span>$</span><input class="input" id="sc-loss" type="number" value="200" placeholder="200"></div>
            </div>
            <div class="input-group">
              <label>Win Rate (%)</label>
              <div class="input-prefix"><span>%</span><input class="input" id="sc-wr" type="number" value="50" min="1" max="99" placeholder="50"></div>
            </div>
            <button class="btn btn-primary" id="sc-calc-btn" style="width:100%">Calculate Metrics</button>
          </div>
          <div class="card" id="sc-results" style="display:none">
            <div class="card-header"><span class="card-title">📈 Advanced Metrics</span></div>
            <div id="sc-result-grid" class="stack" style="gap:8px"></div>
          </div>
        </div>
      </div>
    `;
  },

  bindTabs() {
    Utils.qsa('[data-tab]', Utils.qs('#calc-tabs')).forEach(tab => {
      tab.addEventListener('click', () => {
        Utils.qsa('.tab', Utils.qs('#calc-tabs')).forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const name = tab.dataset.tab;
        Utils.qsa('.tab-panel').forEach(p => p.style.display = 'none');
        const panel = Utils.qs(`#tab-${name}`);
        if (panel) { panel.style.display = ''; panel.classList.add('animate-in'); }
      });
    });
    // Scenario calc
    const scBtn = Utils.qs('#sc-calc-btn');
    if (scBtn) scBtn.addEventListener('click', () => this.calcScenarios());
  },

  bindPositionForm() {
    const btn = Utils.qs('#ps-calc-btn');
    if (btn) btn.addEventListener('click', () => this.calcPosition());

    // Live pips hint
    const entryEl = Utils.qs('#ps-entry');
    const slEl    = Utils.qs('#ps-sl');
    const update  = () => {
      const e = parseFloat(entryEl?.value), s = parseFloat(slEl?.value);
      const hint = Utils.qs('#ps-pips-hint');
      if (hint && e && s && e !== s) {
        const pips = Math.abs(e - s) / 0.0001;
        hint.textContent = `Distance: ${pips.toFixed(1)} pips`;
      }
    };
    entryEl?.addEventListener('input', update);
    slEl?.addEventListener('input', update);

    // Instrument → symbols
    const inst = Utils.qs('#ps-instrument');
    const sym  = Utils.qs('#ps-symbol');
    const symbolMap = {
      forex:  ['EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CHF','USD/CAD'],
      crypto: ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT'],
    };
    inst?.addEventListener('change', () => {
      const opts = symbolMap[inst.value] || [];
      sym.innerHTML = opts.map(s => `<option>${s}</option>`).join('');
    });
  },

  bindMultiTPForm() {
    let tpCount = 3;

    Utils.qs('#tp-add-btn')?.addEventListener('click', () => {
      tpCount++;
      const container = Utils.qs('#tp-levels-container');
      const div = document.createElement('div');
      div.innerHTML = this.renderTPLevel(tpCount);
      container.appendChild(div.firstElementChild);
      this.bindRemoveButtons();
    });

    Utils.qs('#tp-calc-btn')?.addEventListener('click', () => this.calcMultiTP());
    this.bindRemoveButtons();
  },

  bindRemoveButtons() {
    Utils.qsa('.tp-remove-btn').forEach(btn => {
      btn.onclick = () => {
        const level = btn.closest('[data-tp-level]');
        if (level) level.remove();
      };
    });
  },

  async calcPosition() {
    const btn = Utils.qs('#ps-calc-btn');
    btn.textContent = '⏳ Calculating...'; btn.disabled = true;
    try {
      const payload = {
        account_balance: parseFloat(Utils.qs('#ps-balance').value),
        risk_pct:        parseFloat(Utils.qs('#ps-risk').value),
        entry_price:     parseFloat(Utils.qs('#ps-entry').value),
        stop_loss:       parseFloat(Utils.qs('#ps-sl').value),
        instrument:      Utils.qs('#ps-instrument').value,
        symbol:          Utils.qs('#ps-symbol').value,
      };

      const r = await API.calcSize(payload);
      if (!r.ok || !r.data?.ok) {
        const msg = r.data?.errors ? Object.values(r.data.errors).join(', ') : 'Calculation failed';
        Utils.toast(msg, 'error'); return;
      }
      this._lastCalc = { ...payload, ...r.data };
      this.showPositionResults(r.data);

      // Bind copy/save
      Utils.qs('#ps-copy-btn').onclick = () => {
        const d = r.data;
        Utils.copy(`Position: ${d.lot_size} lots | Risk: ${Utils.fmt.currency(d.risk_usd)} (${d.actual_risk_pct}%) | Margin: ${Utils.fmt.currency(d.margin_required)}`);
      };
      Utils.qs('#ps-save-btn').onclick = () => this.saveCalc(payload, r.data);
    } finally {
      btn.textContent = '⚡ Calculate Position Size'; btn.disabled = false;
    }
  },

  showPositionResults(d) {
    const grid = Utils.qs('#ps-result-grid');
    const wrap = Utils.qs('#ps-results');
    if (!grid || !wrap) return;
    wrap.style.display = '';
    grid.innerHTML = [
      { label:'Position Size',  value: Utils.fmt.lots(d.lot_size), cls:'green' },
      { label:'Risk Amount',    value: Utils.fmt.currency(d.risk_usd), cls:'yellow' },
      { label:'Risk %',         value: Utils.fmt.pct(d.actual_risk_pct) },
      { label:'Position Value', value: Utils.fmt.currency(d.position_value) },
      { label:'Margin Required',value: Utils.fmt.currency(d.margin_required), cls:'red' },
      { label:'Available Margin',value: Utils.fmt.currency(d.remaining_margin), cls:'green' },
      { label:'Max Drawdown',   value: Utils.fmt.pct(d.max_drawdown_pct) },
      { label:'Pips at Risk',   value: Utils.fmt.pips(d.pips_risk / 0.0001) },
    ].map(i => `
      <div class="result-item">
        <div class="label">${i.label}</div>
        <div class="value ${i.cls||''}">${i.value}</div>
      </div>
    `).join('');
    wrap.classList.add('animate-in');
  },

  async calcMultiTP() {
    const btn = Utils.qs('#tp-calc-btn');
    btn.textContent = '⏳ Calculating...'; btn.disabled = true;
    try {
      const levels = [];
      Utils.qsa('[data-tp-level]').forEach(row => {
        const price = parseFloat(row.querySelector('.tp-price')?.value);
        const qty   = parseFloat(row.querySelector('.tp-qty')?.value);
        if (price && qty) levels.push({ price, qty_pct: qty });
      });

      if (!levels.length) { Utils.toast('Add at least one TP level', 'error'); return; }

      const payload = {
        entry:     parseFloat(Utils.qs('#tp-entry').value),
        stop_loss: parseFloat(Utils.qs('#tp-sl').value),
        lot_size:  parseFloat(Utils.qs('#tp-lots').value),
        tp_levels: levels,
      };

      const r = await API.calcTP(payload);
      if (!r.ok || !r.data?.ok) { Utils.toast('Calculation failed','error'); return; }
      this.showTPResults(r.data);
    } finally {
      btn.textContent = '⚡ Calculate Breakdown'; btn.disabled = false;
    }
  },

  showTPResults(d) {
    const wrap = Utils.qs('#tp-results');
    const tbl  = Utils.qs('#tp-breakdown-table');
    const sum  = Utils.qs('#tp-summary-grid');
    if (!wrap) return;
    wrap.style.display = '';

    tbl.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:0.82rem">
        <thead>
          <tr style="color:var(--text-tertiary);border-bottom:1px solid var(--border-secondary)">
            <th style="padding:6px 8px;text-align:left">TP</th>
            <th style="padding:6px 8px;text-align:right">Price</th>
            <th style="padding:6px 8px;text-align:right">Pips</th>
            <th style="padding:6px 8px;text-align:right">Qty</th>
            <th style="padding:6px 8px;text-align:right">Profit</th>
            <th style="padding:6px 8px;text-align:right">R:R</th>
          </tr>
        </thead>
        <tbody>
          ${d.tp_breakdown.map((tp, i) => `
            <tr style="border-bottom:1px solid var(--border-secondary)">
              <td style="padding:8px;font-weight:700;color:var(--info)">TP${i+1}</td>
              <td style="padding:8px;text-align:right;font-family:var(--font-mono)">${Utils.fmt.price(tp.price)}</td>
              <td style="padding:8px;text-align:right;font-family:var(--font-mono)">${tp.pips}</td>
              <td style="padding:8px;text-align:right">${tp.qty_pct}%</td>
              <td style="padding:8px;text-align:right;color:var(--success);font-family:var(--font-mono)">${Utils.fmt.currency(tp.profit)}</td>
              <td style="padding:8px;text-align:right;font-family:var(--font-mono)">${tp.risk_reward}:1</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;

    sum.innerHTML = [
      { label:'Total Potential Profit',  value: Utils.fmt.currency(d.total_potential_profit), cls:'green' },
      { label:'Potential Loss (SL hit)', value: Utils.fmt.currency(d.potential_loss), cls:'red' },
      { label:'Expected Value (50% WR)', value: Utils.fmt.currency(d.expected_value), cls: d.expected_value>0?'green':'red' },
      { label:'Reward:Risk Ratio',       value: `${d.reward_risk_ratio}:1`, cls: d.reward_risk_ratio>=1?'green':'yellow' },
    ].map(i => `
      <div class="result-item">
        <div class="label">${i.label}</div>
        <div class="value ${i.cls}">${i.value}</div>
      </div>
    `).join('');
    wrap.classList.add('animate-in');
  },

  calcScenarios() {
    const avgWin  = parseFloat(Utils.qs('#sc-win')?.value)  || 0;
    const avgLoss = parseFloat(Utils.qs('#sc-loss')?.value) || 0;
    const winRate = parseFloat(Utils.qs('#sc-wr')?.value)   / 100 || 0.5;
    const lossRate = 1 - winRate;

    const breakEvenWR    = avgLoss / (avgWin + avgLoss);
    const expectancy     = (winRate * avgWin) - (lossRate * avgLoss);
    const profitFactor   = (winRate * avgWin) / (lossRate * avgLoss) || 0;
    const kellyPct       = ((winRate * avgWin - lossRate * avgLoss) / avgWin) * 100;
    const fractionalKelly= kellyPct / 4;

    const wrap = Utils.qs('#sc-results');
    const grid = Utils.qs('#sc-result-grid');
    if (!wrap || !grid) return;
    wrap.style.display = '';

    grid.innerHTML = [
      { label:'Break-Even Win Rate',   value: Utils.fmt.pct(breakEvenWR*100), cls: winRate > breakEvenWR ? 'green':'red' },
      { label:'Expectancy per Trade',  value: Utils.fmt.currency(expectancy), cls: expectancy>0?'green':'red' },
      { label:'Profit Factor',         value: profitFactor.toFixed(2), cls: profitFactor>=2?'green': profitFactor>=1?'yellow':'red' },
      { label:'Kelly Criterion',       value: Utils.fmt.pct(kellyPct) },
      { label:'Fractional Kelly (÷4)', value: Utils.fmt.pct(fractionalKelly), cls:'green' },
      { label:'Annual (25 trades)',    value: Utils.fmt.currency(expectancy*25) },
    ].map(i => `
      <div class="result-item">
        <div class="label">${i.label}</div>
        <div class="value ${i.cls||''}">${i.value}</div>
      </div>
    `).join('');
    wrap.classList.add('animate-in');
  },

  saveCalc(payload, result) {
    const entry = {
      id:        Date.now(),
      symbol:    payload.symbol,
      entry:     payload.entry_price,
      sl:        payload.stop_loss,
      lot_size:  result.lot_size,
      risk_usd:  result.risk_usd,
      timestamp: new Date().toLocaleString(),
    };
    this.savedCalcs.unshift(entry);
    if (this.savedCalcs.length > 20) this.savedCalcs.pop();
    Utils.storage.set('risk_calcs', this.savedCalcs);
    this.loadSaved();
    Utils.toast('Setup saved!', 'success');
  },

  loadSaved() {
    this.savedCalcs = Utils.storage.get('risk_calcs', []);
    const list = Utils.qs('#ps-history-list');
    if (!list) return;
    if (!this.savedCalcs.length) {
      list.innerHTML = '<p style="color:var(--text-tertiary);font-size:0.8rem;padding:8px 0">No saved setups yet.</p>';
      return;
    }
    list.innerHTML = this.savedCalcs.map(c => `
      <div class="signal-row" style="grid-template-columns:1fr auto;padding:8px 0">
        <div>
          <div style="font-family:var(--font-mono);font-size:0.85rem;font-weight:700">${c.symbol}</div>
          <div style="font-size:0.75rem;color:var(--text-tertiary)">Entry: ${c.entry} | SL: ${c.sl} | ${Utils.fmt.lots(c.lot_size)} | Risk: ${Utils.fmt.currency(c.risk_usd)}</div>
          <div style="font-size:0.72rem;color:var(--text-tertiary)">${c.timestamp}</div>
        </div>
        <button class="btn btn-sm btn-danger" onclick="RiskCalc.deleteCalc(${c.id})">✕</button>
      </div>
    `).join('');

    Utils.qs('#ps-clear-btn')?.addEventListener('click', () => {
      this.savedCalcs = [];
      Utils.storage.set('risk_calcs', []);
      this.loadSaved();
    });
  },

  deleteCalc(id) {
    this.savedCalcs = this.savedCalcs.filter(c => c.id !== id);
    Utils.storage.set('risk_calcs', this.savedCalcs);
    this.loadSaved();
  },
};

function initRiskCalculator() { RiskCalc.init(); }
window.RiskCalc = RiskCalc;
