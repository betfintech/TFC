"""web/routes/api.py — JSON API endpoints"""
from __future__ import annotations
from flask import Blueprint, request, jsonify
from core.logger import get_logger
log = get_logger("web.routes.api")
bp = Blueprint("api", __name__, url_prefix="/api")


# ── EXISTING ENDPOINTS ──

@bp.route("/status", methods=["GET"])
def api_status():
    try:
        from trading.runtime_state import state as runtime_state
        snap = runtime_state.snapshot()
        return jsonify({"ok": True, **snap})
    except Exception as exc:
        log.error("GET /api/status: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/signals", methods=["GET"])
def api_signals():
    try:
        from communication.signal_store import get_recent
        limit = min(int(request.args.get("limit", 50)), 200)
        data = get_recent(limit)
        return jsonify(data if isinstance(data, list) else [])
    except Exception as exc:
        log.error("GET /api/signals: %s", exc)
        return jsonify([])


@bp.route("/candles", methods=["GET"])
def api_candles():
    try:
        raw_sym = request.args.get("symbol", "EURUSD").upper().strip()
        tf      = request.args.get("tf", "M1").upper().strip()
        limit   = max(1, min(int(request.args.get("limit", 500)), 1000))

        # ── Normalise symbol: try both "EURUSD" and "EUR/USD" formats ──────────
        from data.candle_engine import get_candles_tv, _SYMBOL_MAP, _fetch_historical

        # Build candidate keys: raw, with-slash, without-slash
        sym_noslash = raw_sym.replace("/", "")
        sym_slash   = raw_sym[:3] + "/" + raw_sym[3:] if "/" not in raw_sym and len(raw_sym) == 6 else raw_sym

        candles = []

        # Try engine cache with both key formats
        for key in (raw_sym, sym_noslash, sym_slash):
            candles = get_candles_tv(key, tf, limit=limit)
            if candles:
                log.debug("api_candles: engine hit for key=%r tf=%s → %d candles", key, tf, len(candles))
                break

        # ── If engine is empty (not seeded yet), do a direct Deriv fetch ───────
        if not candles:
            log.info("api_candles: engine empty for %s %s — direct Deriv fetch", raw_sym, tf)
            deriv_sym = None
            for key in (raw_sym, sym_noslash, sym_slash):
                deriv_sym = _SYMBOL_MAP.get(key)
                if deriv_sym:
                    break

            if deriv_sym:
                hist = _fetch_historical(deriv_sym, tf, count=limit)
                if hist:
                    candles = [
                        {"time": c["timestamp"], "open": c["open"],
                         "high": c["high"], "low": c["low"], "close": c["close"]}
                        for c in hist
                    ]
                    log.info("api_candles: direct Deriv fetch → %d candles for %s %s", len(candles), deriv_sym, tf)
            else:
                log.warning("api_candles: no Deriv symbol mapping for %r", raw_sym)

        # ── Last resort: unified router (Binance fallback for crypto) ──────────
        if not candles:
            from trading.market.unified import get_candles as uc
            raw = uc(raw_sym, tf, limit=limit)
            if raw:
                candles = [
                    {"time": c["timestamp"], "open": c["open"],
                     "high": c["high"], "low": c["low"], "close": c["close"]}
                    for c in raw
                ]

        return jsonify(candles or [])

    except Exception as exc:
        log.error("GET /api/candles: %s", exc)
        return jsonify([])


@bp.route("/chat", methods=["POST"])
def api_chat():
    try:
        from chat.openrouter_brain import get_response
        user_text = request.json.get("message", "").strip()
        user_id   = int(request.json.get("user_id", 0))
        reply = get_response(user_text, user_id)
        return jsonify({"reply": reply or "I'm not sure. Try asking about signals."})
    except Exception as exc:
        log.error("POST /api/chat: %s", exc)
        return jsonify({"reply": "⚠️ Error processing your message."}), 500


# ── NEW: Risk Calculator endpoints ──

@bp.route("/calc/position-size", methods=["POST"])
def calc_position_size():
    """
    POST /api/calc/position-size
    {account_balance, risk_pct, entry_price, stop_loss, instrument, symbol}
    """
    try:
        from web.utils.validators import validate_position_calc
        from web.utils.constants import PIP_VALUES, LEVERAGE

        data   = request.get_json() or {}
        errors = validate_position_calc(data)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400

        balance    = float(data["account_balance"])
        risk_pct   = float(data["risk_pct"]) / 100
        entry      = float(data["entry_price"])
        stop       = float(data["stop_loss"])
        symbol     = data.get("symbol", "EUR/USD")
        instrument = data.get("instrument", "forex")

        pips_risk    = abs(entry - stop)
        pip_value    = PIP_VALUES.get(instrument, {}).get(symbol, 10.0)
        risk_usd     = balance * risk_pct
        lot_size     = risk_usd / (pips_risk * pip_value) if (pips_risk * pip_value) else 0
        pos_value    = entry * lot_size * 100_000
        margin_req   = pos_value / LEVERAGE.get(instrument, 50)

        return jsonify({
            "ok": True,
            "lot_size":          round(lot_size, 2),
            "risk_usd":          round(risk_usd, 2),
            "pips_risk":         round(pips_risk, 5),
            "actual_risk_pct":   round(risk_pct * 100, 2),
            "position_value":    round(pos_value, 2),
            "margin_required":   round(margin_req, 2),
            "remaining_margin":  round(balance - margin_req, 2),
            "max_drawdown_pct":  round(risk_pct * 100, 2),
        })
    except Exception as exc:
        log.error("POST /api/calc/position-size: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/calc/tp-breakdown", methods=["POST"])
def calc_tp_breakdown():
    """
    POST /api/calc/tp-breakdown
    {entry, stop_loss, lot_size, tp_levels:[{price, qty_pct}]}
    """
    try:
        data      = request.get_json() or {}
        entry     = float(data["entry"])
        stop      = float(data["stop_loss"])
        lot_size  = float(data["lot_size"])
        tp_levels = data.get("tp_levels", [])
        pip_value = 10.0  # standard forex

        results, total_profit = [], 0
        pips_risk = abs(entry - stop)

        for tp in tp_levels:
            price    = float(tp["price"])
            qty_pct  = float(tp.get("qty_pct", 0)) / 100
            qty      = lot_size * qty_pct
            pips_g   = abs(price - entry)
            profit   = pips_g * qty * pip_value / 0.0001  # normalize to pip units
            rr       = round((price - entry) / (entry - stop), 2) if (entry - stop) else 0

            results.append({
                "price":       price,
                "qty_pct":     qty_pct * 100,
                "qty_lots":    round(qty, 2),
                "pips":        round(pips_g / 0.0001, 1),
                "profit":      round(profit, 2),
                "risk_reward": rr,
            })
            total_profit += profit

        loss_if_sl = (pips_risk / 0.0001) * lot_size * pip_value
        ev = (total_profit * 0.5) - (loss_if_sl * 0.5)

        return jsonify({
            "ok": True,
            "tp_breakdown":          results,
            "total_potential_profit": round(total_profit, 2),
            "potential_loss":         round(loss_if_sl, 2),
            "expected_value":         round(ev, 2),
            "reward_risk_ratio":      round(total_profit / loss_if_sl, 2) if loss_if_sl > 0 else 0,
        })
    except Exception as exc:
        log.error("POST /api/calc/tp-breakdown: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/calc/save", methods=["POST"])
def calc_save():
    try:
        # TODO: wire up user auth & persistence
        return jsonify({"ok": True, "message": "Setup saved (requires authentication)"})
    except Exception as exc:
        log.error("POST /api/calc/save: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Account endpoints ──

@bp.route("/account/profile", methods=["GET"])
def account_profile():
    return jsonify({
        "user_id": 0,
        "username": "trader",
        "subscription_status": "active",
        "plan": "Premium",
    })


@bp.route("/account/settings", methods=["POST"])
def account_settings():
    return jsonify({"ok": True})
