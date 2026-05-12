"""
trading/engine.py — Production-Grade SMC Trading Engine
=========================================================
Continuously scans all configured pairs, generates EMA Crossover signals via the
strategy pipeline, applies discipline controls, deduplicates signals, and
dispatches clean signals through the notifier.

NEVER executes trades.

Production guarantees:
  ✓ Never crashes the system — every pair is wrapped independently
  ✓ Never stops silently — heartbeat logs on every cycle
  ✓ Scan interval: 60–120 s (configurable via MAIN_LOOP_INTERVAL in config)
  ✓ Full candle validation before strategy is called
  ✓ Signal deduplication — same direction suppressed unless 4h elapsed or opposite side appeared
  ✓ Signal quality gate — entry/SL/TP must be valid numbers
  ✓ Shared runtime state updated every cycle (readable by chatbot / web)
  ✓ API errors per-pair are logged and skipped; loop always continues
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from threading import Event

from core.config import (
    MAIN_LOOP_INTERVAL,
    CRYPTO_PAIRS,
    FOREX_PAIRS,
    TF_H1,
    TF_M15,
    TF_M1,
    TF_M5,
    SIGNAL_DROUGHT_ALERT_HOURS,
)
from core.logger import get_logger
from trading.discipline import Discipline
from trading.strategy import Signal, generate_signal
from trading.market.unified import get_candles, market_type
from trading.runtime_state import state as runtime_state
from communication.notifier import send_signal

log = get_logger(__name__)

# ── Self-learning integration (optional — fails gracefully if not ready) ───────
try:
    from learning.rl_agent import RLAgent, state_from_signal, reward as rl_reward
    from learning.regime_detector import RegimeDetector
    from learning.outcome_tracker import OutcomeTracker
    _LEARNING_AVAILABLE = True
except ImportError:
    _LEARNING_AVAILABLE = False

# Global learning instances — set by app.py after initialization
_memory      = None
_rl_agent: "RLAgent | None"         = None
_regime_det: "RegimeDetector | None" = None
_outcome_tracker: "OutcomeTracker | None" = None


def inject_learning(memory, rl_agent, regime_det, outcome_tracker) -> None:
    """Called from app.py to inject learning components into the engine."""
    global _memory, _rl_agent, _regime_det, _outcome_tracker
    _memory          = memory
    _rl_agent        = rl_agent
    _regime_det      = regime_det
    _outcome_tracker = outcome_tracker
    log.info("[Engine] Learning components injected: RL=%s, Regime=%s, Tracker=%s",
             rl_agent is not None, regime_det is not None, outcome_tracker is not None)

# ── Scan interval ──────────────────────────────────────────────────────────────
# M15-based setups form inside candles, not only at candle close.
# 60 s gives fresh signals; 120 s is the upper practical bound.
# MAIN_LOOP_INTERVAL is read from config (env: MAIN_LOOP_INTERVAL).
# Values outside 60–120 are clamped so a misconfigured env cannot break timing.
_MIN_INTERVAL = 60
_MAX_INTERVAL = 120
_SCAN_INTERVAL: int = max(_MIN_INTERVAL, min(_MAX_INTERVAL, int(MAIN_LOOP_INTERVAL)))

# ── Candle minimums ────────────────────────────────────────────────────────────
# Strategy needs slow_EMA(50) + crossover_lookback(3) + 5 = 58 H1 bars minimum.
# Set to 60 to provide a small safety margin.
_MIN_H1_CANDLES  = 60
_MIN_M15_CANDLES = 20


class TradingEngine:
    """
    Continuously scans all configured pairs, generates SMC signals,
    applies discipline checks, and dispatches signals via notifier.
    Never executes trades.
    """

    ALL_PAIRS: list[str] = CRYPTO_PAIRS + FOREX_PAIRS

    def __init__(self) -> None:
        self._discipline = Discipline()
        self._stop_event = Event()

        # ── In-memory deduplication store ─────────────────────────────────────
        # Maps symbol → last dispatched signal direction ("BUY" | "SELL").
        # Dedup is SOFTENED: a repeated direction is allowed again if:
        #   (a) the opposite direction has been sent in between, OR
        #   (b) DEDUP_RESET_HOURS have passed since the last send.
        # This lets drought-broken pairs re-signal without waiting forever.
        self._last_sent: dict[str, str] = {}
        self._last_sent_time: dict[str, float] = {}   # symbol → monotonic timestamp
        self._DEDUP_RESET_HOURS: float = 4.0           # allow same direction after 4 h
        self._last_any_signal_time: float = time.monotonic()  # global drought tracker

    # ── Public API ─────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the engine to stop after the current cycle completes."""
        self._stop_event.set()

    def run(self) -> None:
        """
        Main engine loop.

        Structure:
            loop forever (until stop()):
                log cycle start
                process every pair (each independently fault-tolerant)
                log cycle complete
                sleep SCAN_INTERVAL
        """
        runtime_state.mark_running()
        log.info(
            "Trading engine started | pairs: %s | scan interval: %ds",
            self.ALL_PAIRS,
            _SCAN_INTERVAL,
        )

        while not self._stop_event.is_set():
            try:
                self._scan_cycle()
            except Exception as exc:
                # This guard should never fire — _scan_cycle is itself fully
                # wrapped — but it is here as the ultimate safety net so that
                # an unforeseen error never kills the engine thread.
                err_msg = f"Unexpected engine-level error: {exc}"
                log.error(err_msg, exc_info=True)
                runtime_state.record_error(err_msg)

            # Interruptible sleep: wakes immediately on stop()
            self._stop_event.wait(timeout=_SCAN_INTERVAL)

        runtime_state.mark_stopped()
        log.info("Trading engine stopped cleanly.")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _scan_cycle(self) -> None:
        """
        One full scan of all pairs.
        Each pair is wrapped in its own try/except so one failure cannot
        prevent the remaining pairs from being processed.
        """
        cycle_start = datetime.now(timezone.utc)
        runtime_state.update_scan_start()

        log.info(
            "-- Scan cycle #%d started -- %s UTC -- %d pairs",
            runtime_state.snapshot()["cycle_count"],
            cycle_start.strftime("%H:%M:%S"),
            len(self.ALL_PAIRS),
        )

        pairs_processed = 0

        for symbol in self.ALL_PAIRS:
            if self._stop_event.is_set():
                log.info("Stop requested — aborting cycle mid-scan.")
                break

            log.info("Processing pair: %s", symbol)

            try:
                self._process_symbol(symbol)
                pairs_processed += 1
            except Exception as exc:
                # Per-pair isolation: log and continue — never halt the loop
                err_msg = f"[{symbol}] Unhandled error in _process_symbol: {exc}"
                log.error(err_msg, exc_info=True)
                runtime_state.record_error(err_msg)

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        runtime_state.update_scan_complete(pairs_processed)

        log.info(
            "-- Scan cycle #%d completed -- %d/%d pairs processed -- %.1fs elapsed",
            runtime_state.snapshot()["cycle_count"],
            pairs_processed,
            len(self.ALL_PAIRS),
            elapsed,
        )

        # ── Global drought alert ───────────────────────────────────────────────
        # Warn if the whole bot has been silent for too long — helps detect
        # data-feed issues or overly tight filters before they go unnoticed.
        hours_quiet = (time.monotonic() - self._last_any_signal_time) / 3600
        if hours_quiet >= SIGNAL_DROUGHT_ALERT_HOURS:
            log.warning(
                "DROUGHT ALERT: No signal sent in %.1f hours (threshold: %dh). "
                "Check data feeds, pair configs, and strategy conditions.",
                hours_quiet, SIGNAL_DROUGHT_ALERT_HOURS,
            )

    def _process_symbol(self, symbol: str) -> None:
        """
        Full processing pipeline for one symbol.

        Steps:
          [1] Discipline check (cooldown / hourly / daily limits)
          [2] Fetch candles (H1 + M15)
          [3] Validate candle data
          [4] Generate strategy signal
          [5] Quality gate (actionable + valid levels)
          [6] Deduplication gate
          [7] Record discipline + update runtime state + dispatch
        """
        mtype = market_type(symbol)

        # ── [1] Discipline check ───────────────────────────────────────────────
        allowed, reason = self._discipline.can_signal(symbol)
        if not allowed:
            log.debug("[%s] Discipline skip: %s", symbol, reason)
            return

        # ── [2] Fetch candles ──────────────────────────────────────────────────
        log.debug("[%s] Fetching candles (H1 + M15)", symbol)
        try:
            h1_raw  = get_candles(symbol, TF_H1,  limit=100)
            m15_raw = get_candles(symbol, TF_M15, limit=100)
        except Exception as exc:
            log.warning("[%s] Candle fetch raised exception: %s", symbol, exc)
            return

        # ── [3] Candle validation ──────────────────────────────────────────────
        valid, validation_reason = self._validate_candles(symbol, h1_raw, m15_raw)
        if not valid:
            log.warning("[%s] Candle validation failed: %s", symbol, validation_reason)
            return

        # ── [4] Generate signal (with audit for learning) ──────────────────────
        try:
            if _LEARNING_AVAILABLE and _rl_agent is not None:
                from trading.strategy import generate_signal_with_audit
                signal, gate_audit = generate_signal_with_audit(symbol, h1_raw, m15_raw, mtype)
            else:
                signal = generate_signal(symbol, h1_raw, m15_raw, mtype)
                gate_audit = {}
        except Exception as exc:
            log.error("[%s] Strategy raised exception: %s", symbol, exc, exc_info=True)
            return

        log.info("[%s] Signal generated: %s -- %s", symbol, signal.direction, signal.reason)

        if signal.direction == "WAIT":
            return

        # ── [4b] Regime detection ──────────────────────────────────────────────
        regime = "UNKNOWN"
        try:
            if _regime_det is not None:
                regime = _regime_det.detect(h1_raw)
                gate_audit["regime"] = regime
                if not _regime_det.should_trade_in_regime(regime):
                    log.info(
                        "[%s] Regime '%s' historically poor — signal suppressed by regime filter",
                        symbol, regime,
                    )
                    return
        except Exception as exc:
            log.debug("[%s] Regime detection error: %s", symbol, exc)

        # ── [4c] RL agent action selection ────────────────────────────────────
        rl_state  = None
        rl_action = "TAKE_SIGNAL"
        try:
            if _rl_agent is not None:
                from learning.rl_agent import state_from_signal
                rl_state  = state_from_signal(signal, gate_audit, regime)
                rl_action = _rl_agent.choose_action(rl_state)

                if rl_action == "SKIP_SIGNAL":
                    log.info("[%s] RL agent suppressed signal", symbol)
                    return

                signal = _rl_agent.apply_action(rl_action, signal)
        except Exception as exc:
            log.debug("[%s] RL agent error: %s", symbol, exc)

        # ── [5] Signal quality gate ────────────────────────────────────────────
        if not self._passes_quality_gate(signal):
            return

        # ── [6] Deduplication gate (softened) ────────────────────────────────
        last_dir  = self._last_sent.get(symbol)
        last_time = self._last_sent_time.get(symbol, 0.0)
        same_direction = last_dir == signal.direction
        if same_direction:
            hours_since = (time.monotonic() - last_time) / 3600
            if hours_since < self._DEDUP_RESET_HOURS:
                log.debug(
                    "[%s] Duplicate suppressed -- %s already sent %.1fh ago (resets at %.0fh)",
                    symbol, signal.direction, hours_since, self._DEDUP_RESET_HOURS,
                )
                return
            else:
                log.info(
                    "[%s] Same direction %s allowed — %.1fh since last send (drought reset)",
                    symbol, signal.direction, hours_since,
                )

        # ── [7] Dispatch ───────────────────────────────────────────────────────
        self._discipline.record_signal(symbol)
        self._last_sent[symbol] = signal.direction
        self._last_sent_time[symbol] = time.monotonic()
        self._last_any_signal_time = time.monotonic()  # reset global drought clock
        runtime_state.record_signal(symbol, signal.direction)

        log.info(
            "[%s] %s signal dispatching -> entry=%.5f SL=%.5f TP1=%.5f",
            symbol, signal.direction, signal.entry, signal.stop_loss, signal.tp1,
        )
        send_signal(signal)

        # ── [7b] Outcome tracking + RL update ─────────────────────────────────
        try:
            if _outcome_tracker is not None and _rl_agent is not None:
                _captured_state  = rl_state
                _captured_action = rl_action
                _captured_signal = signal
                _captured_regime = regime

                def _on_outcome(outcome: str) -> None:
                    try:
                        if _memory is not None:
                            sig_dict = {
                                "symbol": _captured_signal.symbol,
                                "direction": _captured_signal.direction,
                                "entry": _captured_signal.entry,
                                "stop_loss": _captured_signal.stop_loss,
                                "tp1": _captured_signal.tp1,
                            }
                            _memory.record_live_signal_outcome(sig_dict, outcome)

                        if _rl_agent is not None and _captured_state is not None:
                            from learning.rl_agent import reward as rl_reward, state_from_signal
                            rr = 0.0
                            if outcome == "TP1_HIT":
                                rr_risk = abs(_captured_signal.entry - _captured_signal.stop_loss)
                                rr = abs(_captured_signal.tp1 - _captured_signal.entry) / rr_risk if rr_risk > 0 else 2.0
                            r = rl_reward(_captured_action, outcome, rr)
                            next_state = _captured_state  # terminal — reuse
                            _rl_agent.update(_captured_state, _captured_action, r, next_state)

                            if _memory is not None and _regime_det is not None:
                                _memory.record_regime(
                                    _captured_regime, outcome in ("TP1_HIT", "TP2_HIT"), rr
                                )
                    except Exception as exc:
                        log.debug("[%s] Outcome callback error: %s", symbol, exc)

                _outcome_tracker.track(signal, _on_outcome)
        except Exception as exc:
            log.debug("[%s] Outcome tracker setup error: %s", symbol, exc)

    # ── Validation helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_candles(
        symbol: str,
        h1_raw: object,
        m15_raw: object,
    ) -> tuple[bool, str]:
        """
        Validate raw candle data before handing it to the strategy.

        Checks:
          - Both feeds are non-empty lists
          - Minimum candle counts are met (H1>=30, M15>=20)
          - No candle has a zero or negative close price

        Returns (valid, reason).
        """
        if not h1_raw or not isinstance(h1_raw, list):
            return False, "H1 candle feed is empty or invalid"
        if not m15_raw or not isinstance(m15_raw, list):
            return False, "M15 candle feed is empty or invalid"

        if len(h1_raw) < _MIN_H1_CANDLES:
            return False, (
                f"Insufficient H1 candles: {len(h1_raw)} < {_MIN_H1_CANDLES} required"
            )
        if len(m15_raw) < _MIN_M15_CANDLES:
            return False, (
                f"Insufficient M15 candles: {len(m15_raw)} < {_MIN_M15_CANDLES} required"
            )

        # Price sanity on the most recent candles (strategy catches the rest)
        for label, candles in (("H1", h1_raw[-5:]), ("M15", m15_raw[-5:])):
            for c in candles:
                if not isinstance(c, dict):
                    return False, f"{label} candle is not a dict: {c!r}"
                price = c.get("close", 0)
                if not isinstance(price, (int, float)) or price <= 0:
                    return False, f"{label} candle has invalid close price: {price!r}"

        return True, "OK"

    @staticmethod
    def _passes_quality_gate(signal: Signal) -> bool:
        """
        Final quality check before a BUY/SELL signal leaves the engine.

        Rejects the signal if:
          - direction is not BUY or SELL
          - is_actionable is False
          - entry, stop_loss, or tp1 are zero, negative, or NaN/Inf
        """
        if not signal.is_actionable:
            log.warning(
                "[%s] Invalid signal blocked -- direction not actionable: %s",
                signal.symbol, signal.direction,
            )
            return False

        def _bad(v: float) -> bool:
            return not isinstance(v, (int, float)) or v <= 0 or not math.isfinite(v)

        for field_name, value in (
            ("entry",     signal.entry),
            ("stop_loss", signal.stop_loss),
            ("tp1",       signal.tp1),
        ):
            if _bad(value):
                log.warning(
                    "[%s] Invalid signal blocked -- %s is invalid: %s",
                    signal.symbol, field_name, value,
                )
                return False

        return True
        