/**
 * api-client.js — Centralized fetch wrapper
 */

const API = {
  async _fetch(url, opts={}) {
    try {
      const res = await fetch(url, {
        headers: {'Content-Type':'application/json'},
        ...opts,
      });
      const data = await res.json();
      return { ok: res.ok, status: res.status, data };
    } catch(e) {
      console.error('API error:', url, e);
      return { ok: false, error: e.message, data: null };
    }
  },

  get:  (url, params={}) => {
    const q = new URLSearchParams(params).toString();
    return API._fetch(q ? `${url}?${q}` : url);
  },
  post: (url, body={}) => API._fetch(url, { method:'POST', body: JSON.stringify(body) }),

  // ── Endpoints ──
  status:   ()       => API.get('/api/status'),
  signals:  (limit=50) => API.get('/api/signals', {limit}),
  candles:  (sym, tf, limit=500) => API.get('/api/candles', {symbol:sym, tf, limit}),
  chat:     (msg, uid=0) => API.post('/api/chat', {message:msg, user_id:uid}),
  calcSize: (p)      => API.post('/api/calc/position-size', p),
  calcTP:   (p)      => API.post('/api/calc/tp-breakdown', p),
  calcSave: (p)      => API.post('/api/calc/save', p),
  profile:  ()       => API.get('/api/account/profile'),
};

window.API = API;
