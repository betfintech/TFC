"""
communication/formatter.py — Signal message formatter (PRODUCTION-GRADE)
=========================================================================
HARDENING:
  - All formatting wrapped in safe guards — NEVER raises an exception
  - Timestamp: falls back to "Unknown time" if None, invalid, or missing isoformat
  - Price values: fall back to "N/A" if None, invalid float, or NaN/Inf
  - direction/symbol/market_type: safe string coercion with fallbacks
  - format_signal is guaranteed to return a non-empty string
  - format_wait is safe against None symbol/reason
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.strategy import Signal


def _safe_price(val, decimals: int = 5) -> str:
    """Safely format a numeric price value. Returns 'N/A' on any failure."""
    if val is None:
        return "N/A"
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return "N/A"
        return f"{f:.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _safe_str(val, fallback: str = "Unknown") -> str:
    """Safely coerce a value to string. Returns fallback if None/empty."""
    if val is None:
        return fallback
    try:
        s = str(val).strip()
        return s if s else fallback
    except Exception:
        return fallback


def _safe_timestamp(ts) -> str:
    """
    Safely format a timestamp to 'YYYY-MM-DD HH:MM UTC'.
    Handles datetime objects, ISO strings, None, and invalid values.
    """
    if ts is None:
        return "Unknown time"
    try:
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M UTC")
        # Try parsing ISO string
        if isinstance(ts, str) and ts.strip():
            dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        return "Unknown time"
    except Exception:
        return "Unknown time"


def format_signal(signal: Signal) -> str:
    """
    Format a signal into a clean Telegram-ready Markdown message with emoji.

    GUARANTEED to return a non-empty string — never raises.
    Falls back gracefully on any invalid or missing field.
    """
    try:
        direction   = _safe_str(getattr(signal, "direction", None), "UNKNOWN")
        symbol      = _safe_str(getattr(signal, "symbol", None), "UNKNOWN")
        market_type = _safe_str(getattr(signal, "market_type", None), "unknown")
        reason      = _safe_str(getattr(signal, "reason", None), "No analysis available")
        timestamp   = _safe_timestamp(getattr(signal, "timestamp", None))

        direction_emoji = "🟢" if direction == "BUY" else "🔴"
        market_emoji    = "₿" if market_type.lower() == "crypto" else "💱"

        # Determine decimal precision safely
        try:
            entry_val = float(getattr(signal, "entry", 0) or 0)
            dec = 2 if market_type.lower() == "crypto" and entry_val > 100 else 5
        except (TypeError, ValueError):
            dec = 5

        entry    = _safe_price(getattr(signal, "entry", None), dec)
        stop_loss= _safe_price(getattr(signal, "stop_loss", None), dec)
        tp1      = _safe_price(getattr(signal, "tp1", None), dec)
        tp2      = _safe_price(getattr(signal, "tp2", None), dec)
        tp_final = _safe_price(getattr(signal, "tp_final", None), dec)

        lines = [
            f"{direction_emoji} *{direction} SIGNAL* {market_emoji}",
            f"",
            f"📌 *Symbol:*  `{symbol}`",
            f"🏷️ *Market:*  {market_type.capitalize()}",
            f"",
            f"🎯 *Entry:*       `{entry}`",
            f"🛑 *Stop Loss:*   `{stop_loss}`",
            f"",
            f"💰 *TP1 (1:2):*  `{tp1}`",
            f"💰 *TP2 (1:3):*  `{tp2}`",
            f"🏁 *Final TP:*   `{tp_final}`",
            f"",
            f"📊 *Analysis:*",
            f"_{reason}_",
            f"",
            f"⏰ `{timestamp}`",
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ _Signal only. Not financial advice._",
        ]
        return "\n".join(lines)

    except Exception as exc:
        # Ultimate fallback — formatter NEVER crashes the caller
        try:
            sym = _safe_str(getattr(signal, "symbol", None), "?")
            dirn = _safe_str(getattr(signal, "direction", None), "?")
            return f"⚠️ Signal: {dirn} {sym}\n_(formatting error — raw signal)_"
        except Exception:
            return "⚠️ Signal received (formatting unavailable)"


def format_wait(symbol: str, reason: str) -> str:
    """Format a WAIT message safely."""
    sym    = _safe_str(symbol, "?")
    rsn    = _safe_str(reason, "No reason given")
    return f"⏳ [{sym}] WAIT — {rsn}"
