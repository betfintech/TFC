/**
 * chart-manager.js — TradingView Lightweight Charts integration
 */

const ChartManager = {
  chart: null,
  series: null,
  symbol: 'EURUSD',
  tf: 'M1',

  init() {
    this.render();
    this.loadData();
  },

  render() {
    const el = Utils.qs('#chart-root');
    if (!el) return;
    el.innerHTML = `
      <div class="card" style="padding:12px;margin-bottom:12px">
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <select class="input select" id="chart-symbol" style="width:140px">
            <option value="EURUSD">EUR/USD</option><option value="GBPUSD">GBP/USD</option>
            <option value="USDJPY">USD/JPY</option><option value="BTCUSDT">BTC/USDT</option>
          </select>
          <div class="row" style="gap:4px">
            ${['M1','M5','M15','H1','D1'].map(tf => `
              <button class="btn btn-sm ${tf===this.tf?'btn-primary':'btn-secondary'} chart-tf-btn" data-tf="${tf}">${tf}</button>
            `).join('')}
          </div>
          <button class="btn btn-sm btn-secondary" id="chart-refresh-btn">🔄 Refresh</button>
        </div>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <div id="chart-container" style="height:420px;position:relative">
          <div id="chart-loading" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:var(--bg-secondary)">
            <div class="spinner"></div>
          </div>
        </div>
      </div>
    `;

    Utils.qs('#chart-symbol').addEventListener('change', e => {
      this.symbol = e.target.value; this.loadData();
    });
    Utils.qsa('.chart-tf-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        Utils.qsa('.chart-tf-btn').forEach(b => b.classList.replace('btn-primary','btn-secondary'));
        btn.classList.replace('btn-secondary','btn-primary');
        this.tf = btn.dataset.tf; this.loadData();
      });
    });
    Utils.qs('#chart-refresh-btn').addEventListener('click', () => this.loadData());
  },

  async loadData() {
    const loading = Utils.qs('#chart-loading');
    if (loading) loading.style.display = 'flex';

    const r = await API.candles(this.symbol, this.tf, 300);
    if (loading) loading.style.display = 'none';

    if (!r.ok || !Array.isArray(r.data) || !r.data.length) {
      const c = Utils.qs('#chart-container');
      if (c) c.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-tertiary)">No candle data available</div>';
      return;
    }

    this.initChart(r.data);
  },

  initChart(candles) {
    const container = Utils.qs('#chart-container');
    if (!container) return;

    if (typeof LightweightCharts === 'undefined') {
      container.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-tertiary);flex-direction:column;gap:8px">
          <div>📈</div><p>Chart library loading…</p>
          <p style="font-size:0.75rem">Include lightweight-charts CDN in base.html</p>
        </div>`;
      return;
    }

    if (this.chart) { this.chart.remove(); this.chart = null; }

    this.chart = LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: 420,
      layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
    });

    this.series = this.chart.addCandlestickSeries({
      upColor: '#3fb950', downColor: '#f85149',
      borderUpColor: '#3fb950', borderDownColor: '#f85149',
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
    });

    this.series.setData(candles.map(c => ({
      time: typeof c.time === 'string' ? Math.floor(new Date(c.time).getTime()/1000) : c.time,
      open: c.open, high: c.high, low: c.low, close: c.close,
    })));

    this.chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      this.chart?.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
  },
};

function initChart() { ChartManager.init(); }
window.ChartManager = ChartManager;
