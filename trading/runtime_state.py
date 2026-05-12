"""
trading/runtime_state.py
========================
Lightweight shared in-memory state for the engine.

READ-ONLY for chatbot and web layer.
WRITE-ONLY from TradingEngine.

No external dependencies. Thread-safe via a single Lock.
Import this anywhere — it is safe to do so from multiple threads.

Usage:
    from trading.runtime_state import state
    state.update_scan(...)     # engine writes
    snap = state.snapshot()    # anyone reads
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional


@dataclass
class _State:
    """All mutable fields live here. Access only through RuntimeState methods."""
    engine_status: str = "starting"
    last_scan_time: Optional[datetime] = None
    last_signal_direction: Optional[str] = None
    last_signal_symbol: Optional[str] = None
    last_signal_time: Optional[datetime] = None
    pairs_scanned_last_cycle: int = 0
    signals_sent_today: int = 0
    cycle_count: int = 0
    last_error: Optional[str] = None


class RuntimeState:
    """
    Thread-safe engine state store.

    The engine calls the `update_*` methods.
    Anything else (chatbot, web) calls `snapshot()`.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._s = _State()

    # ── Engine writes ──────────────────────────────────────────────────────────

    def mark_running(self) -> None:
        with self._lock:
            self._s.engine_status = "running"

    def mark_stopped(self) -> None:
        with self._lock:
            self._s.engine_status = "stopped"

    def update_scan_start(self) -> None:
        with self._lock:
            self._s.last_scan_time = datetime.now(timezone.utc)
            self._s.cycle_count += 1

    def update_scan_complete(self, pairs_scanned: int) -> None:
        with self._lock:
            self._s.pairs_scanned_last_cycle = pairs_scanned

    def record_signal(self, symbol: str, direction: str) -> None:
        with self._lock:
            self._s.last_signal_direction = direction
            self._s.last_signal_symbol = symbol
            self._s.last_signal_time = datetime.now(timezone.utc)
            self._s.signals_sent_today += 1

    def record_error(self, error: str) -> None:
        with self._lock:
            self._s.last_error = error

    def reset_daily_count(self) -> None:
        with self._lock:
            self._s.signals_sent_today = 0

    # ── Read-only snapshot (safe for chatbot / web) ────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            s = self._s
            return {
                "engine_status": s.engine_status,
                "last_scan_time": s.last_scan_time.isoformat() if s.last_scan_time else None,
                "last_signal": {
                    "symbol": s.last_signal_symbol,
                    "direction": s.last_signal_direction,
                    "time": s.last_signal_time.isoformat() if s.last_signal_time else None,
                },
                "pairs_scanned_last_cycle": s.pairs_scanned_last_cycle,
                "signals_sent_today": s.signals_sent_today,
                "cycle_count": s.cycle_count,
                "last_error": s.last_error,
            }


# ── Module-level singleton — import this directly ──────────────────────────────
state = RuntimeState()
