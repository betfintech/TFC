/**
 * utils.js — Helpers: formatters, storage, misc
 */

const Utils = {
  fmt: {
    price:    (v, d=4)  => Number(v).toFixed(d),
    currency: (v)       => `$${Number(v).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})}`,
    pct:      (v, d=2)  => `${Number(v).toFixed(d)}%`,
    lots:     (v)       => `${Number(v).toFixed(2)} lots`,
    pips:     (v)       => `${Number(v).toFixed(1)} pips`,
    timeAgo:  (ts) => {
      const d = Math.floor((Date.now() - new Date(ts)) / 1000);
      if (d < 60)  return `${d}s ago`;
      if (d < 3600) return `${Math.floor(d/60)}m ago`;
      return `${Math.floor(d/3600)}h ago`;
    },
    utcNow: () => new Date().toUTCString().slice(17,25) + ' UTC',
  },

  storage: {
    get:    (k, def=null) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch { return def; } },
    set:    (k, v)        => { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    remove: (k)           => { try { localStorage.removeItem(k); return true; } catch { return false; } },
  },

  toast: (msg, type='info', duration=3000) => {
    const c = document.getElementById('toast-container') || (() => {
      const el = document.createElement('div');
      el.id = 'toast-container';
      el.className = 'toast-container';
      document.body.appendChild(el);
      return el;
    })();
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), duration);
  },

  copy: async (text) => {
    try { await navigator.clipboard.writeText(text); Utils.toast('Copied!','success'); }
    catch { Utils.toast('Copy failed','error'); }
  },

  debounce: (fn, ms=300) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  },

  qs:  (sel, ctx=document) => ctx.querySelector(sel),
  qsa: (sel, ctx=document) => [...ctx.querySelectorAll(sel)],
};

window.Utils = Utils;
