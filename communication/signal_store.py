"""
communication/signal_store.py — Thread-safe in-memory + disk store (PRODUCTION-GRADE)
=======================================================================================
HARDENING v2:
  - Duplicate protection: skips identical signal (same symbol+direction+entry)
  - Direction validation: rejects entries where direction is not BUY or SELL
  - Symbol validation: rejects entries where symbol is empty
  - Corrupted entries are skipped individually (never crash the whole load)
  - Atomic disk writes (via atomic_write_json) — no partial writes on crash
  - Thread safety: all mutations under Lock
  - get_recent always returns a list, never raises
"""
from __future__ import annotations

import os
from collections import deque
from threading import Lock
from typing import Any

from core.logger import get_logger
from core.utils import atomic_write_json, load_json_safe

log = get_logger(__name__)

_MAX_SIGNALS = 20
_DATA_DIR    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_STORE_FILE  = os.path.join(_DATA_DIR, "recent_signals.json")

_lock   = Lock()
_buffer: deque[dict] = deque(maxlen=_MAX_SIGNALS)
_loaded = False

_VALID_DIRECTIONS = {"BUY", "SELL"}


def _ensure_data_dir() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
    except OSError as exc:
        log.warning("Could not create data directory %s: %s", _DATA_DIR, exc)


def _load_from_disk() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    _ensure_data_dir()

    items = load_json_safe(_STORE_FILE, default=[])
    if not isinstance(items, list):
        log.warning("signal_store: expected list in %s, got %s — resetting", _STORE_FILE, type(items))
        items = []

    loaded = 0
    skipped = 0
    for item in items[-_MAX_SIGNALS:]:
        # Skip corrupted entries
        if not isinstance(item, dict):
            skipped += 1
            continue
        # Skip entries with invalid direction
        direction = str(item.get("direction", "")).strip().upper()
        if direction not in _VALID_DIRECTIONS:
            log.debug("signal_store: skipping entry with invalid direction=%r", direction)
            skipped += 1
            continue
        # Skip entries with empty symbol
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            log.debug("signal_store: skipping entry with empty symbol")
            skipped += 1
            continue
        _buffer.append(item)
        loaded += 1

    if skipped:
        log.warning("signal_store: skipped %d corrupted/invalid entries on load", skipped)
    log.debug("Loaded %d valid signals from disk", loaded)


def _save_to_disk() -> None:
    _ensure_data_dir()
    success = atomic_write_json(_STORE_FILE, list(_buffer))
    if not success:
        log.error("signal_store: failed to persist signals to disk")


def _safe_str(val: Any, field: str = "?") -> str:
    try:
        return str(val) if val is not None else ""
    except Exception:
        return f"<invalid {field}>"


def _is_duplicate_entry(entry: dict) -> bool:
    """Return True if an identical signal (symbol+direction+entry price) already exists."""
    key = (
        entry.get("symbol", ""),
        entry.get("direction", ""),
        round(float(entry.get("entry", 0.0) or 0.0), 5),
    )
    for existing in _buffer:
        existing_key = (
            existing.get("symbol", ""),
            existing.get("direction", ""),
            round(float(existing.get("entry", 0.0) or 0.0), 5),
        )
        if key == existing_key:
            return True
    return False


def record_signal(signal: Any) -> None:
    """
    Store a Signal object for the dashboard.

    Validates fields before storing:
      - direction must be BUY or SELL
      - symbol must not be empty
      - duplicate (same symbol+direction+entry) is skipped

    Never raises.
    """
    if signal is None:
        log.warning("signal_store.record_signal: received None signal — skipped")
        return

    try:
        with _lock:
            _load_from_disk()

            # ── Field validation ──────────────────────────────────────────────
            direction = _safe_str(getattr(signal, "direction", ""), "direction").strip().upper()
            if direction not in _VALID_DIRECTIONS:
                log.warning(
                    "signal_store: rejected signal with invalid direction=%r (must be BUY or SELL)",
                    direction,
                )
                return

            symbol = _safe_str(getattr(signal, "symbol", ""), "symbol").strip()
            if not symbol:
                log.warning("signal_store: rejected signal with empty symbol")
                return

            # ── Build entry ───────────────────────────────────────────────────
            try:
                entry_price = float(getattr(signal, "entry", 0) or 0)
            except (TypeError, ValueError):
                entry_price = 0.0

            entry: dict = {
                "symbol":      symbol,
                "direction":   direction,
                "market_type": _safe_str(getattr(signal, "market_type", ""), "market_type"),
                "entry":       entry_price,
                "stop_loss":   float(getattr(signal, "stop_loss", 0) or 0),
                "tp1":         float(getattr(signal, "tp1", 0) or 0),
                "tp2":         float(getattr(signal, "tp2", 0) or 0),
                "tp_final":    float(getattr(signal, "tp_final", 0) or 0),
                "reason":      _safe_str(getattr(signal, "reason", ""), "reason"),
                "timestamp":   (
                    signal.timestamp.isoformat()
                    if hasattr(signal, "timestamp") and hasattr(signal.timestamp, "isoformat")
                    else str(getattr(signal, "timestamp", ""))
                ),
            }

            # ── Duplicate check ───────────────────────────────────────────────
            if _is_duplicate_entry(entry):
                log.debug(
                    "signal_store: duplicate signal skipped (%s %s @ %s)",
                    direction, symbol, entry_price,
                )
                return

            _buffer.append(entry)
            _save_to_disk()

    except Exception as exc:
        log.error("signal_store.record_signal failed: %s", exc, exc_info=True)


def get_recent(n: int = 20) -> list[dict]:
    """Return the most recent n signals (newest first). Always returns a list."""
    try:
        with _lock:
            _load_from_disk()
            return list(reversed(list(_buffer)))[:max(1, n)]
    except Exception as exc:
        log.error("signal_store.get_recent failed: %s", exc)
        return []
