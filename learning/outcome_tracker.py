"""
learning/outcome_tracker.py — Live Signal Outcome Tracker
==========================================================
After a live signal is dispatched, monitors candle data forward in time
to determine if TP1, TP2, SL was hit, or if the signal expired.

Non-blocking — each signal spawns a short-lived daemon thread.
Outcome is fed back to MemoryStore and RLAgent.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from core.logger import get_logger
from trading.strategy import Signal

log = get_logger(__name__)

# Check every 15 minutes for 48 hours max
_CHECK_INTERVAL_SECS = 900    # 15 min
_MAX_CHECKS          = 192    # 48 hours / 15 min


class OutcomeTracker:
    """
    Tracks live signal outcomes by polling candle data after dispatch.

    Non-blocking: spawns a background thread per signal that wakes up
    every 15 minutes to check TP/SL status.
    """

    def __init__(self) -> None:
        self._active: list[threading.Thread] = []
        self._lock = threading.Lock()   # guards _active list

    def track(self, signal: Signal, callback_fn: Callable[[str], None]) -> None:
        """
        Begin tracking a signal outcome in the background.

        Args:
            signal:      The dispatched Signal object.
            callback_fn: Called with the outcome string when determined.
        """
        t = threading.Thread(
            target=self._monitor_loop,
            args=(signal, callback_fn),
            daemon=True,
            name=f"OutcomeTracker-{signal.symbol}-{signal.direction}",
        )
        t.start()
        with self._lock:
            self._active.append(t)
            # Prune finished threads (keep list bounded)
            self._active = [x for x in self._active if x.is_alive()]

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _monitor_loop(self, signal: Signal, callback_fn: Callable[[str], None]) -> None:
        """Background thread that polls for outcome every 15 minutes."""
        # Initial wait — minimum 1 candle before checking
        time.sleep(_CHECK_INTERVAL_SECS)

        for check_num in range(_MAX_CHECKS):
            try:
                outcome = self._check_outcome(signal)
                if outcome:
                    log.info(
                        "[OutcomeTracker] %s %s → %s (check #%d)",
                        signal.symbol, signal.direction, outcome, check_num + 1,
                    )
                    try:
                        callback_fn(outcome)
                    except Exception as exc:
                        log.error("[OutcomeTracker] callback error: %s", exc)
                    return

            except Exception as exc:
                log.debug("[OutcomeTracker] check error for %s: %s", signal.symbol, exc)

            time.sleep(_CHECK_INTERVAL_SECS)

        # Expired
        log.info("[OutcomeTracker] %s %s → EXPIRED after %dh",
                 signal.symbol, signal.direction, _MAX_CHECKS * _CHECK_INTERVAL_SECS // 3600)
        try:
            callback_fn("EXPIRED")
        except Exception as exc:
            log.error("[OutcomeTracker] callback error on expire: %s", exc)

    @staticmethod
    def _check_outcome(signal: Signal) -> Optional[str]:
        """
        Fetch the latest candle for signal.symbol and check if TP/SL was hit.
        Returns outcome string or None if undecided.
        """
        try:
            from trading.market.unified import get_candles
            from core.config import TF_M15

            candles = get_candles(signal.symbol, TF_M15, limit=10)
            if not candles or not isinstance(candles, list):
                return None

            for candle in candles:
                high  = candle.get("high",  0.0)
                low   = candle.get("low",   0.0)

                if signal.direction == "BUY":
                    if low <= signal.stop_loss:
                        return "SL_HIT"
                    if signal.tp2 and high >= signal.tp2:
                        return "TP2_HIT"
                    if high >= signal.tp1:
                        return "TP1_HIT"
                else:  # SELL
                    if high >= signal.stop_loss:
                        return "SL_HIT"
                    if signal.tp2 and low <= signal.tp2:
                        return "TP2_HIT"
                    if low <= signal.tp1:
                        return "TP1_HIT"

            return None
        except Exception as exc:
            log.debug("[OutcomeTracker] _check_outcome error: %s", exc)
            return None
