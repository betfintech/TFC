"""
web/routes.py — Dashboard routes + Candle API + WebSocket + File Upload (FIXED)
================================================================================
FIXES:
1. Added proper 404 error handling with graceful fallbacks
2. Templates rendered with better error recovery
3. All routes return valid content (no empty 404s)
4. File cleanup to reduce storage bloat
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, render_template, request

from core.logger import get_logger

log = get_logger("web.routes")

# Upload configuration
UPLOAD_FOLDER = '/tmp/chat_uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'webp'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _cleanup_old_uploads(max_age_hours=24):
    """Remove upload files older than max_age_hours to prevent bloat."""
    try:
        import time
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                file_age = now - os.path.getmtime(filepath)
                if file_age > max_age_seconds:
                    try:
                        os.remove(filepath)
                        log.debug("Cleaned up old upload: %s", filename)
                    except Exception as e:
                        log.warning("Could not remove file %s: %s", filename, e)
    except Exception as e:
        log.debug("Upload cleanup skipped: %s", e)


def register_routes(app: Flask) -> None:

    # ── Existing routes (with improved error handling) ───────────────────────────

    @app.route("/")
    def index():
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            return render_template("index.html", now=now)
        except Exception as exc:
            log.warning("Route /: template error: %s", exc)
            return _index_inline_fallback(), 200

    def _index_inline_fallback():
        """Fallback if index.html fails to render."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return f"""<!DOCTYPE html>
<html><head><title>Trading Signal Dashboard</title>
<style>body{{font-family:sans-serif;max-width:1200px;margin:50px auto;padding:20px;background:#f5f5f5}}
h1{{color:#333}}.nav{{margin:20px 0}}.nav a{{margin-right:15px;padding:8px 12px;background:#007bff;
color:white;text-decoration:none;border-radius:4px}}.nav a:hover{{background:#0056b3}}
.status{{background:white;padding:15px;border-radius:8px;margin:20px 0}}
</style></head><body>
<h1>🚀 Trading Signal Dashboard</h1>
<p>Server time: <strong>{now}</strong></p>
<div class="nav">
<a href="/signals">📊 Signals</a>
<a href="/status">📈 Status</a>
<a href="/chart">📉 Chart</a>
<a href="/payments">💳 Payments</a>
<a href="/chat">💬 Chat</a>
</div>
<div class="status">
<h3>System Status</h3>
<p>✅ Dashboard is online</p>
<p>Use the navigation above to view signals, charts, and payment information.</p>
</div>
</body></html>"""

    @app.route("/status")
    def status():
        try:
            return render_template("status.html")
        except Exception as exc:
            log.warning("Route /status: template error: %s", exc)
            return jsonify({"ok": True, "message": "Status page — see /api/status for data"}), 200

    @app.route("/signals")
    def signals():
        signals_data = []
        try:
            from communication.signal_store import get_recent
            signals_data = get_recent(20)
            if not isinstance(signals_data, list):
                signals_data = []
        except Exception as exc:
            log.warning("Route /signals: error fetching data: %s", exc)
        
        try:
            return render_template("signals.html", signals=signals_data)
        except Exception as exc:
            log.warning("Route /signals: template error: %s", exc)
            return _signals_inline_fallback(signals_data), 200

    def _signals_inline_fallback(signals):
        """Fallback if signals.html fails to render."""
        html = """<!DOCTYPE html>
<html><head><title>Recent Signals</title>
<style>body{font-family:sans-serif;max-width:1200px;margin:50px auto;padding:20px;background:#f5f5f5}
table{width:100%;border-collapse:collapse;background:white}
th,td{padding:12px;text-align:left;border-bottom:1px solid #ddd}
th{background:#007bff;color:white}
tr:hover{background:#f0f0f0}
.buy{color:green;font-weight:bold}.sell{color:red;font-weight:bold}
</style></head><body>
<h1>📊 Recent Signals</h1>
<table>
<tr><th>Symbol</th><th>Direction</th><th>Entry</th><th>Stop Loss</th><th>TP1</th><th>Timestamp</th></tr>
"""
        if signals:
            for sig in signals[:20]:
                if isinstance(sig, dict):
                    direction = sig.get("direction", "?")
                    direction_class = "buy" if direction == "BUY" else "sell"
                    html += f"""<tr>
<td>{sig.get('symbol', '?')}</td>
<td class="{direction_class}">{direction}</td>
<td>{sig.get('entry', '?')}</td>
<td>{sig.get('stop_loss', '?')}</td>
<td>{sig.get('tp1', '?')}</td>
<td>{sig.get('timestamp', '?')}</td>
</tr>"""
        else:
            html += "<tr><td colspan='6' style='text-align:center'>No signals yet</td></tr>"
        html += "</table><br><a href='/'>← Back to Dashboard</a></body></html>"
        return html

    @app.route("/payments")
    def payments():
        pending     = []
        subscribers = []
        try:
            from payment.storage import get_pending
            raw_pending = get_pending()
            pending = list(raw_pending.values()) if isinstance(raw_pending, dict) else []
        except Exception as exc:
            log.warning("Route /payments: pending error: %s", exc)
        try:
            from payment.storage import get_all_subscribers
            subscribers = get_all_subscribers()
            if not isinstance(subscribers, list):
                subscribers = []
        except Exception as exc:
            log.warning("Route /payments: subscribers error: %s", exc)
        
        try:
            return render_template("payments.html", pending=pending, subscribers=subscribers)
        except Exception as exc:
            log.warning("Route /payments: template error: %s", exc)
            return _payments_inline_fallback(pending, subscribers), 200

    def _payments_inline_fallback(pending, subscribers):
        """Fallback if payments.html fails to render."""
        return f"""<!DOCTYPE html>
<html><head><title>Payment Management</title>
<style>body{{font-family:sans-serif;max-width:1200px;margin:50px auto;padding:20px;background:#f5f5f5}}
h2{{color:#333}}.box{{background:white;padding:15px;border-radius:8px;margin:15px 0}}
</style></head><body>
<h1>💳 Payment Management</h1>
<div class="box">
<h2>⏳ Pending Approvals</h2>
<p><strong>{len(pending)}</strong> pending payment submissions</p>
</div>
<div class="box">
<h2>✅ Active Subscribers</h2>
<p><strong>{len(subscribers)}</strong> active subscriptions</p>
</div>
<br><a href="/">← Back to Dashboard</a></body></html>"""

    @app.route("/api/status")
    def api_status():
        try:
            from trading.runtime_state import state as runtime_state
            snap = runtime_state.snapshot()
            return jsonify({"ok": True, **snap})
        except Exception as exc:
            log.error("Route /api/status: error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    # ── Candle REST endpoint ──────────────────────────────────────────────────

    _SYM_NORM = {
        "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD", "USDCHF": "USD/CHF", "USDCAD": "USD/CAD",
        "NZDUSD": "NZD/USD", "BTCUSD": "BTC/USD", "BTCUSDT": "BTC/USD",
        "EUR/USD": "EUR/USD", "GBP/USD": "GBP/USD", "USD/JPY": "USD/JPY",
        "AUD/USD": "AUD/USD", "USD/CHF": "USD/CHF", "USD/CAD": "USD/CAD",
        "NZD/USD": "NZD/USD", "BTC/USD": "BTC/USD",
    }

    @app.route("/api/candles")
    def api_candles():
        """
        GET /api/candles?symbol=EURUSD&tf=M1&limit=200

        Returns TradingView Lightweight Charts format:
        [{"time": <epoch>, "open": f, "high": f, "low": f, "close": f}, ...]
        """
        try:
            raw_sym = request.args.get("symbol", "EURUSD").upper().strip()
            symbol  = _SYM_NORM.get(raw_sym, "EUR/USD")
            tf      = request.args.get("tf", "M1").upper().strip()
            limit   = int(request.args.get("limit", 200))
            limit   = max(1, min(limit, 500))

            from data.candle_engine import get_candles_tv
            candles = get_candles_tv(symbol, tf, limit=limit)

            if not candles:
                # Try strategy-format fallback
                from trading.market.unified import get_candles as uc
                raw = uc(symbol, tf, limit=limit)
                if raw:
                    candles = [
                        {"time": c["timestamp"], "open": c["open"],
                         "high": c["high"], "low": c["low"], "close": c["close"]}
                        for c in raw
                    ]

            return jsonify(candles or [])
        except Exception as exc:
            log.error("Route /api/candles: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── Chart page ───────────────────────────────────────────────────────

    @app.route("/chart")
    def chart():
        try:
            return render_template("chart.html")
        except Exception as exc:
            log.warning("Route /chart: template error: %s", exc)
            return _chart_inline_fallback(), 200

    def _chart_inline_fallback():
        return """<!DOCTYPE html>
<html><head><title>Live Chart</title>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<style>body{background:#0d1117;color:#e6edf3;font-family:sans-serif;margin:0}</style>
</head><body style="padding:20px"><h2>Live Candlestick Chart</h2>
<div id="chart" style="width:100%;height:500px"></div>
<script>
const chart = LightweightCharts.createChart(document.getElementById('chart'),
  {width:window.innerWidth-40,height:500,layout:{background:{color:'#0d1117'},textColor:'#e6edf3'},
   grid:{vertLines:{color:'#30363d'},hLines:{color:'#30363d'}}});
const series = chart.addCandlestickSeries();
async function load(){
  try{
    const r = await fetch('/api/candles?symbol=EURUSD&tf=M1&limit=200');
    const d = await r.json();
    if(Array.isArray(d) && d.length>0) series.setData(d);
  }catch(e){console.error('Chart load error:',e)}
}
load();
setInterval(load,10000);
</script></body></html>"""

    # ── Web chat page ────────────────────────────────────────────────────

    @app.route("/chat")
    def chat_page():
        try:
            return render_template("chat.html")
        except Exception as exc:
            log.warning("Route /chat: template error: %s", exc)
            return "<h2>Chat unavailable</h2><p>Please try again later.</p>", 500

    # ── Chat API endpoint with file upload ──────────────────────────────────

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        """
        POST /api/chat
        
        Accepts either JSON or FormData:
        
        JSON Body:
          {"message": "...", "user_id": 12345}
        
        FormData (with file):
          - message (text)
          - user_id (int)
          - file (binary, optional)
        
        Returns: {"reply": "..."}
        """
        try:
            # Cleanup old uploads periodically
            _cleanup_old_uploads(max_age_hours=24)
            
            user_text = ""
            user_id = 0
            file_info = None
            
            # Check if this is JSON or FormData
            content_type = request.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                # Handle JSON request
                data = request.get_json(silent=True) or {}
                user_text = str(data.get("message", "")).strip()
                user_id = int(data.get("user_id", 0))
            else:
                # Handle FormData request
                user_text = request.form.get("message", "").strip()
                user_id = int(request.form.get("user_id", 0))
                
                # Handle file upload
                if 'file' in request.files:
                    file = request.files['file']
                    
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        
                        # Check file size
                        file.seek(0, os.SEEK_END)
                        file_size = file.tell()
                        file.seek(0)
                        
                        if file_size > MAX_FILE_SIZE:
                            log.warning("File upload too large: %d bytes", file_size)
                            return jsonify({
                                "reply": f"⚠️ File is too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB."
                            })
                        
                        if not allowed_file(filename):
                            log.warning("File type not allowed: %s", filename)
                            return jsonify({
                                "reply": "⚠️ File type not allowed. Allowed: images, PDF, Word docs, text files."
                            })
                        
                        # Save file
                        filepath = os.path.join(UPLOAD_FOLDER, filename)
                        file.save(filepath)
                        
                        file_info = {
                            "filename": filename,
                            "filepath": filepath,
                            "size": file_size
                        }
                        
                        log.info("File uploaded: user_id=%d, filename=%s, size=%d", user_id, filename, file_size)
            
            if not user_text and not file_info:
                return jsonify({"reply": "Please send a message or upload a file! 😊"})
            
            # Log the request
            log.info("Chat request: user_id=%d, message=%s, file=%s", 
                    user_id, user_text[:50] if user_text else "(none)", 
                    file_info["filename"] if file_info else "(none)")
            
            # Prepare context for the AI
            context = {
                "message": user_text,
                "user_id": user_id,
                "file": file_info
            }
            
            # Get AI response — always use openrouter_brain (never static templates)
            if file_info and file_info.get("filepath"):
                # Image uploaded — analyze with vision
                try:
                    with open(file_info["filepath"], "rb") as fh:
                        img_bytes = fh.read()
                    from chat.openrouter_brain import get_response_with_image
                    reply = get_response_with_image(img_bytes, user_text, user_id)
                except Exception as exc:
                    log.error("Image analysis error: %s", exc)
                    reply = (
                        "✅ Your image was received. "
                        "Our admin will review your payment screenshot shortly."
                    )
            else:
                from chat.openrouter_brain import get_response
                reply = get_response(user_text, user_id)
            
            log.info("Chat response: user_id=%d, reply=%s", user_id, reply[:100] if reply else "(empty)")
            
            return jsonify({
                "reply": reply or "I'm not sure how to answer that — try asking about signals, markets, or pricing!"
            })
            
        except Exception as exc:
            log.error("Route /api/chat: error: %s", exc, exc_info=True)
            return jsonify({
                "reply": "⚠️ Something went wrong on my end. Please try again shortly."
            }), 500

    # ── Engine status debug endpoint ──────────────────────────────────────────

    @app.route("/api/engine-status")
    def engine_status():
        try:
            from data.candle_engine import list_symbols, get_candles_tv
            symbols = list_symbols()
            result = {}
            for sym in symbols:
                for tf in ["M1", "M5", "M15", "H1"]:
                    candles = get_candles_tv(sym, tf, limit=5)
                    result.setdefault(sym, {})[tf] = len(candles)
            return jsonify({"tracked_symbols": symbols, "candle_counts": result})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Learning API endpoints ────────────────────────────────────────────────

    def _load_learning_data() -> dict:
        """Load learning.json safely."""
        from core.utils import load_json_safe
        return load_json_safe("data/learning.json", default={})

    @app.route("/api/learning/summary")
    def learning_summary():
        """Overall learning stats."""
        try:
            data  = _load_learning_data()
            per_p = data.get("per_pair", {})
            total_tp1 = total_sigs = 0
            for ps in per_p.values():
                for ds in ps.get("signals", {}).values():
                    total_sigs += ds.get("count", 0)
                    total_tp1  += ds.get("tp1_hit", 0)
            wr = (total_tp1 / total_sigs) if total_sigs > 0 else 0.0
            evolved = data.get("evolved_parameters", {})
            return jsonify({
                "total_bars_processed": data.get("total_bars_processed", 0),
                "total_signals_seen":   total_sigs,
                "overall_win_rate":     round(wr, 4),
                "pairs_tracked":        len(per_p),
                "evolution_generation": evolved.get("generation", 0),
                "evolution_fitness":    evolved.get("fitness_score", 0.0),
                "last_updated":         data.get("last_updated", ""),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/learning/pair/<symbol>")
    def learning_pair(symbol: str):
        """Per-pair learning stats."""
        try:
            data  = _load_learning_data()
            sym   = symbol.replace("-", "/")  # allow URL-safe format
            stats = data.get("per_pair", {}).get(sym, {})
            if not stats:
                return jsonify({"error": f"No data for {sym}"}), 404
            return jsonify(stats)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/learning/regimes")
    def learning_regimes():
        """Market regime win rates."""
        try:
            data = _load_learning_data()
            return jsonify(data.get("market_regimes", {}))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/learning/evolution")
    def learning_evolution():
        """Evolved parameters vs defaults."""
        try:
            from trading.strategy import _PARAMS as _DEFAULT_PARAMS
            data    = _load_learning_data()
            evolved = data.get("evolved_parameters", {})
            comparison = {}
            for k, default_v in _DEFAULT_PARAMS.items():
                comparison[k] = {
                    "default": default_v,
                    "evolved": evolved.get(k, default_v),
                    "changed": evolved.get(k, default_v) != default_v,
                }
            return jsonify({
                "evolved_parameters": evolved,
                "comparison_vs_defaults": comparison,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/learning/progress")
    def learning_progress():
        """Backtest progress (polls live from engine)."""
        try:
            # Try to get live progress from the module-level _learning_memory in app.py
            try:
                import sys as _sys
                _app_mod = _sys.modules.get("app") or _sys.modules.get("__main__")
                if _app_mod is not None:
                    mem = getattr(_app_mod, "_learning_memory", None)
                    if mem is not None:
                        return jsonify(mem.get_backtest_progress())
            except Exception:
                pass
            # Fallback: read progress from the JSON file
            data = _load_learning_data()
            bars = data.get("total_bars_processed", 0)
            return jsonify({
                "running": False,
                "bars_processed": bars,
                "percent": 100.0 if bars > 0 else 0.0,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/learning/gate-performance")
    def learning_gate_performance():
        """Gate win rates for heatmap."""
        try:
            data = _load_learning_data()
            return jsonify(data.get("gate_performance", {}))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/learning")
    def learning_dashboard():
        """Self-learning dashboard page."""
        try:
            data    = _load_learning_data()
            evolved = data.get("evolved_parameters", {})
            per_p   = data.get("per_pair", {})

            # Build pair win-rate table
            pair_rows = []
            for sym, stats in sorted(per_p.items()):
                sigs = sum(s.get("count", 0) for s in stats.get("signals", {}).values())
                tp1s = sum(s.get("tp1_hit", 0) for s in stats.get("signals", {}).values())
                wr   = (tp1s / sigs * 100) if sigs > 0 else 0.0
                pair_rows.append({
                    "symbol": sym,
                    "signals": sigs,
                    "tp1_hit": tp1s,
                    "win_rate": round(wr, 1),
                    "avg_rr": round(stats.get("avg_rr_achieved", 0.0), 2),
                })

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            return render_template(
                "learning.html",
                now=now,
                evolved=evolved,
                pair_rows=pair_rows,
                gate_perf=data.get("gate_performance", {}),
                regimes=data.get("market_regimes", {}),
                total_bars=data.get("total_bars_processed", 0),
                total_signals=sum(r["signals"] for r in pair_rows),
            )
        except Exception as exc:
            log.error("Learning dashboard error: %s", exc)
            return f"<h1>Learning Dashboard</h1><p>Data loading... ({exc})</p>", 200

    # ── Error handlers (improved) ────────────────────────────────────────────

    @app.errorhandler(404)
    def not_found(e):
        """Handle 404 errors gracefully."""
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            return render_template("index.html", now=now, error="Page not found"), 404
        except Exception:
            return f"""<!DOCTYPE html>
<html><head><title>404 Not Found</title>
<style>body{{font-family:sans-serif;margin:50px;text-align:center;color:#333}}</style></head><body>
<h1>404 - Page Not Found</h1>
<p>The page you're looking for doesn't exist.</p>
<p><a href="/">← Back to Dashboard</a></p>
</body></html>""", 404

    @app.errorhandler(500)
    def internal_error(e):
        """Handle 500 errors gracefully."""
        log.error("Flask 500 error: %s", e)
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            return render_template("index.html", now=now, error="Internal server error"), 500
        except Exception:
            return f"""<!DOCTYPE html>
<html><head><title>500 Server Error</title>
<style>body{{font-family:sans-serif;margin:50px;text-align:center;color:#333}}</style></head><body>
<h1>500 - Internal Server Error</h1>
<p>Something went wrong on our end.</p>
<p><a href="/">← Back to Dashboard</a></p>
</body></html>""", 500

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        """Handle unexpected exceptions."""
        log.error("Flask unhandled exception: %s", e, exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500

        
