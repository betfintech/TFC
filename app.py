"""
app.py — Main Entry Point (HARDENED)
======================================
Starts four threads:
  1. Trading engine  — generates & sends signals
  2. Chat bot        — responds to user messages
  3. Payment system  — handles payment proof & admin approval
  4. Web dashboard   — Flask server

HARDENING CHANGES:
  - Each thread wrapped in a crash-resistant safe_thread() with auto-restart
  - Thread death is detected and restarted automatically (max 10 retries)
  - Startup errors are caught gracefully per-component
  - notify_admin failure does NOT crash startup
  - KeyboardInterrupt and signal handling are robust
  - All threads are daemon threads — process exits cleanly on main thread exit
"""
from __future__ import annotations

import os as _os
import sys as _sys

# Ensure repo root is on sys.path regardless of working directory
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

# Load .env before any other import reads os.getenv()
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_os.path.join(_ROOT, ".env"), override=False)
except ImportError:
    pass

import sys
import time
import threading

from core.config import validate
from core.logger import get_logger

log = get_logger("app")

# Module-level reference to MemoryStore — set during main() startup
# Allows web/routes.py to read learning progress without circular imports
_learning_memory = None

_MAX_RESTARTS = 10
_RESTART_DELAY = 5  # seconds between restart attempts


def _safe_thread(name: str, target_fn, max_restarts: int = _MAX_RESTARTS) -> threading.Thread:
    """
    Spawn a daemon thread that auto-restarts on crash (up to max_restarts times).
    Thread death is logged at CRITICAL level and a restart is attempted after a delay.
    """
    def _runner():
        restarts = 0
        while restarts <= max_restarts:
            try:
                log.info("Thread [%s] starting (attempt %d/%d)", name, restarts + 1, max_restarts + 1)
                target_fn()
                # If target_fn returns normally (not via exception), do not restart
                log.warning("Thread [%s] exited normally — not restarting.", name)
                break
            except Exception as exc:
                restarts += 1
                log.critical(
                    "Thread [%s] CRASHED (restart %d/%d): %s",
                    name, restarts, max_restarts, exc,
                    exc_info=True,
                )
                if restarts > max_restarts:
                    log.critical("Thread [%s] exceeded max restarts. Giving up.", name)
                    break
                log.info("Thread [%s] restarting in %ds...", name, _RESTART_DELAY * min(restarts, 5))
                time.sleep(_RESTART_DELAY * min(restarts, 5))

    t = threading.Thread(target=_runner, name=name, daemon=True)
    t.start()
    log.info("Thread started: %s", name)
    return t


def _health_monitor() -> None:
    """
    Lightweight system heartbeat logger.
    - Logs 'SYSTEM ACTIVE' every 5 minutes
    - Warns if no new signals have been stored in the last 2 hours
    - Never crashes the main loop
    """
    import os
    from communication.signal_store import get_recent

    HEARTBEAT_INTERVAL = 300   # 5 minutes
    NO_SIGNAL_WARN_SEC = 7200  # 2 hours

    log.info("[HealthMonitor] Started — heartbeat every %ds", HEARTBEAT_INTERVAL)

    while True:
        try:
            time.sleep(HEARTBEAT_INTERVAL)
            log.info("SYSTEM ACTIVE — trading engine running")

            # Check for stale signal activity
            try:
                recent = get_recent(1)
                if recent:
                    ts_raw = recent[0].get("timestamp", "")
                    if ts_raw:
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - ts).total_seconds()
                        if age > NO_SIGNAL_WARN_SEC:
                            log.warning(
                                "[HealthMonitor] ⚠️ No new signals in %.0f minutes — "
                                "engine may be idle or market is quiet.",
                                age / 60,
                            )
                        else:
                            log.debug("[HealthMonitor] Last signal %.0f minutes ago", age / 60)
                else:
                    log.debug("[HealthMonitor] No signals stored yet")
            except Exception as exc:
                log.debug("[HealthMonitor] Signal age check skipped: %s", exc)

        except Exception as exc:
            log.error("[HealthMonitor] Unexpected error: %s", exc)


def main() -> None:
    log.info("=" * 60)
    log.info("  TRADING SIGNAL SYSTEM — STARTING")
    log.info("  Signal generation only. No trades executed.")
    log.info("=" * 60)

    # Validate required env vars before starting anything
    try:
        validate()
    except EnvironmentError as exc:
        log.critical("Startup failed — missing config: %s", exc)
        sys.exit(1)
    except Exception as exc:
        log.critical("Startup validation error: %s", exc, exc_info=True)
        sys.exit(1)

    # -- Start CandleEngine (Deriv data source) before TradingEngine --
    try:
        from data.candle_engine import start as _candle_start
        from core.config import FOREX_PAIRS, CRYPTO_PAIRS
        _candle_start(symbols=FOREX_PAIRS + CRYPTO_PAIRS)
        log.info("CandleEngine started for %d symbols", len(FOREX_PAIRS + CRYPTO_PAIRS))
    except Exception as exc:
        log.warning("CandleEngine startup error (non-fatal): %s", exc)

    # Import components after validation so failures are caught individually
    engine = None
    try:
        from trading.engine import TradingEngine
        engine = TradingEngine()
    except ModuleNotFoundError as exc:
        log.critical(
            "Failed to initialize TradingEngine — missing module: %s. "
            "Ensure all files in trading/ are committed and deployed. "
            "Continuing without trading engine.",
            exc,
        )
    except Exception as exc:
        log.critical(
            "Failed to initialize TradingEngine: %s. "
            "Continuing without trading engine.",
            exc,
            exc_info=True,
        )

    try:
        from chat.bot import ChatBot
        chatbot = ChatBot()
    except Exception as exc:
        log.critical("Failed to initialize ChatBot: %s", exc, exc_info=True)
        sys.exit(1)

    try:
        from payment.system import PaymentSystem
        payment = PaymentSystem()
    except Exception as exc:
        log.critical("Failed to initialize PaymentSystem: %s", exc, exc_info=True)
        sys.exit(1)

    try:
        from web.server import run_web_server
    except Exception as exc:
        log.critical("Failed to import web server: %s", exc, exc_info=True)
        sys.exit(1)

    threads = [
        _safe_thread("ChatBot",        chatbot.run),
        _safe_thread("PaymentSystem",  payment.run),
        _safe_thread("WebServer",      run_web_server, max_restarts=5),
        _safe_thread("HealthMonitor",  _health_monitor),
    ]
    if engine is not None:
        threads.insert(0, _safe_thread("TradingEngine", engine.run))
    else:
        log.warning("TradingEngine not started — running in degraded mode (chat/web/payments only).")

    # ── Self-learning pipeline (background — never blocks trading) ────────────
    try:
        from learning.memory import MemoryStore
        from learning.rl_agent import RLAgent
        from learning.regime_detector import RegimeDetector
        from learning.outcome_tracker import OutcomeTracker

        memory       = MemoryStore()
        memory.load()
        rl_agent     = RLAgent(memory)
        regime_det   = RegimeDetector(memory)
        outcome_trk  = OutcomeTracker()

        # Expose at module level so web/routes.py /api/learning/progress can read it
        global _learning_memory
        _learning_memory = memory

        # Inject into trading engine
        if engine is not None:
            try:
                from trading.engine import inject_learning
                inject_learning(memory, rl_agent, regime_det, outcome_trk)
            except Exception as exc:
                log.warning("Could not inject learning into engine: %s", exc)

        def _learning_pipeline() -> None:
            """Runs phases in background. Trading engine is unaffected."""
            try:
                # PHASE 1: Backtest (skip if memory already populated)
                if memory.total_bars_processed < 10_000:
                    log.info("[Learning] Phase 1: Historical backtest starting...")
                    from learning.backtester import HistoricalBacktester
                    bt = HistoricalBacktester(memory)
                    bt.run_all()
                    log.info("[Learning] Phase 1 complete — %d bars", memory.total_bars_processed)
                else:
                    log.info("[Learning] Phase 1 skipped — %d bars already in memory",
                             memory.total_bars_processed)

                # PHASE 2: Genetic Algorithm evolution
                if memory.total_signals_seen > 100:
                    log.info("[Learning] Phase 2: Genetic Algorithm starting...")
                    from learning.evolver import StrategyEvolver, apply_evolved_params
                    evolver = StrategyEvolver(memory)
                    best_params = evolver.evolve(memory.get_all_signal_records())
                    if best_params:
                        memory.evolved_parameters = best_params
                        memory.save()
                        apply_evolved_params(best_params)
                        log.info("[Learning] Phase 2 complete — evolved params applied")
                    else:
                        log.warning("[Learning] Phase 2: GA returned no params — skipped")
                else:
                    log.info("[Learning] Phase 2 skipped — insufficient signals (%d)",
                             memory.total_signals_seen)

                # PHASE 3: RL agent active from this point on
                log.info("[Learning] Phase 3: RL agent active — self-learning from live trades")

            except Exception as exc:
                log.error("[Learning] Pipeline error (non-fatal): %s", exc, exc_info=True)

        threads.append(_safe_thread("LearningPipeline", _learning_pipeline, max_restarts=1))

    except ImportError as exc:
        log.warning("Self-learning modules not available: %s", exc)
    except Exception as exc:
        log.warning("Learning pipeline setup failed (non-fatal): %s", exc)

    # Notify admin — failure must NOT crash startup
    try:
        from communication.notifier import notify_admin
        notify_admin("🚀 Signal system started and running.")
    except Exception as exc:
        log.warning("Could not notify admin on startup: %s", exc)

    log.info("All threads running. Main loop watching for shutdown.")

    # Keep main thread alive; monitor threads and restart if needed
    try:
        while True:
            time.sleep(10)
            # Log alive threads for observability
            alive = [t.name for t in threads if t.is_alive()]
            dead  = [t.name for t in threads if not t.is_alive()]
            if dead:
                log.warning("Dead threads detected: %s | Alive: %s", dead, alive)
            else:
                log.debug("All threads alive: %s", alive)
    except KeyboardInterrupt:
        log.info("Shutdown requested (KeyboardInterrupt) — stopping engine.")
        try:
            engine.stop()
        except Exception as exc:
            log.warning("Error stopping engine: %s", exc)
        try:
            from communication.notifier import notify_admin
            notify_admin("⚠️ Signal system shutting down.")
        except Exception as exc:
            log.warning("Could not notify admin on shutdown: %s", exc)
        log.info("System shutdown complete.")
        sys.exit(0)
    except Exception as exc:
        log.critical("Main loop crashed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
