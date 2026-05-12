"""
learning/memory.py — Long-Term Learning Memory Store
======================================================
Thread-safe, JSON-persisted memory for all bot learning data.
Single source of truth for backtesting results, gate performance,
regime statistics, evolved parameters, RL Q-table, and live signal records.

All writes use core.utils.atomic_write_json for crash-safe persistence.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from core.logger import get_logger
from core.utils import atomic_write_json, load_json_safe

log = get_logger(__name__)

_LEARNING_PATH = "data/learning.json"

# ── Schema version — bump when adding breaking changes ─────────────────────────
_SCHEMA_VERSION = 2


class MemoryStore:
    """
    Thread-safe persistent store for all bot learning data.

    Persists to data/learning.json via atomic writes.
    All public methods are safe to call from multiple threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._default_schema()
        # Raw signal records kept in-memory for GA (not persisted — rebuilt from per_pair)
        self._signal_records: list[dict] = []
        # Backtest progress tracking
        self._backtest_total_pairs: int = 0
        self._backtest_done_pairs: int = 0
        self._backtest_running: bool = False

    # ──────────────────────────────────────────────────────────────────────────
    # Schema
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _default_schema() -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_bars_processed": 0,
            "total_signals_seen": 0,
            "per_pair": {},
            "gate_performance": {},
            "market_regimes": {},
            "evolved_parameters": {},
            "q_table": {},
            "learning_log": [],
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load persisted data from disk. Call once on startup."""
        raw = load_json_safe(_LEARNING_PATH, default={})
        if not raw:
            log.info("[Memory] No existing learning data — starting fresh.")
            self._data = self._default_schema()
            return

        # Migrate old schema if needed
        if raw.get("schema_version", 1) < _SCHEMA_VERSION:
            log.info("[Memory] Migrating learning data to schema v%d", _SCHEMA_VERSION)
            base = self._default_schema()
            base.update(raw)
            raw = base

        with self._lock:
            self._data = raw

        log.info(
            "[Memory] Loaded: %d bars, %d signals, generation=%s",
            self._data.get("total_bars_processed", 0),
            self._data.get("total_signals_seen", 0),
            self._data.get("evolved_parameters", {}).get("generation", 0),
        )

    def save(self) -> bool:
        """Atomically persist all data to disk. Returns True on success."""
        import json as _json
        with self._lock:
            self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
            # Deep-enough copy: serialize to string under lock, then write outside lock.
            # This prevents another thread mutating nested dicts mid-serialization.
            try:
                snapshot_str = _json.dumps(self._data, indent=2, default=str)
            except Exception as exc:
                log.error("[Memory] save(): JSON serialization failed: %s", exc)
                return False

        # Write serialized string atomically (outside lock)
        import os, tempfile
        try:
            dir_path = os.path.dirname(_LEARNING_PATH)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dir_path or ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(snapshot_str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, _LEARNING_PATH)
                return True
            except Exception:
                try: os.unlink(tmp)
                except OSError: pass
                raise
        except Exception as exc:
            log.error("[Memory] save() write failed: %s", exc)
            return False



    # ──────────────────────────────────────────────────────────────────────────
    # Properties (read-only shortcuts)
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def total_bars_processed(self) -> int:
        with self._lock:
            return self._data.get("total_bars_processed", 0)

    @property
    def total_signals_seen(self) -> int:
        with self._lock:
            return self._data.get("total_signals_seen", 0)

    @property
    def evolved_parameters(self) -> dict:
        with self._lock:
            return dict(self._data.get("evolved_parameters", {}))

    @evolved_parameters.setter
    def evolved_parameters(self, params: dict) -> None:
        with self._lock:
            self._data["evolved_parameters"] = dict(params)

    # ──────────────────────────────────────────────────────────────────────────
    # Recording
    # ──────────────────────────────────────────────────────────────────────────

    def record_bar(
        self,
        symbol: str,
        bar_data: dict,
        gate_audit: dict,
        signal: Optional[dict],
        outcome: Optional[str],
    ) -> None:
        """
        Record a processed bar and its signal outcome during backtesting.
        Aggregates per-pair stats and gate performance counters.
        """
        try:
            with self._lock:
                self._data["total_bars_processed"] = self._data.get("total_bars_processed", 0) + 1

                # Per-pair initialization
                per_pair = self._data.setdefault("per_pair", {})
                pair_stats = per_pair.setdefault(symbol, {
                    "total_bars": 0,
                    "signals": {
                        "BUY":  {"count": 0, "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0, "expired": 0},
                        "SELL": {"count": 0, "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0, "expired": 0},
                    },
                    "rr_samples": [],
                    "avg_rr_achieved": 0.0,
                    "best_session": None,
                    "best_structure_quality": None,
                    "best_poi_type": None,
                    "worst_narrative": None,
                })
                pair_stats["total_bars"] = pair_stats.get("total_bars", 0) + 1

                if signal and outcome:
                    direction = signal.get("direction", "")
                    if direction in ("BUY", "SELL"):
                        self._data["total_signals_seen"] = self._data.get("total_signals_seen", 0) + 1
                        sig_stats = pair_stats["signals"][direction]
                        sig_stats["count"] += 1

                        if outcome == "TP1_HIT":
                            sig_stats["tp1_hit"] += 1
                        elif outcome == "TP2_HIT":
                            sig_stats["tp1_hit"] += 1  # TP2 also means TP1 was hit
                            sig_stats["tp2_hit"] += 1
                        elif outcome == "SL_HIT":
                            sig_stats["sl_hit"] += 1
                        elif outcome == "EXPIRED":
                            sig_stats["expired"] += 1

                        # Track RR achieved
                        rr = signal.get("rr_achieved", 0.0)
                        if rr and rr > 0:
                            pair_stats["rr_samples"].append(rr)
                            samples = pair_stats["rr_samples"][-500:]  # keep last 500
                            pair_stats["rr_samples"] = samples
                            pair_stats["avg_rr_achieved"] = sum(samples) / len(samples)

                        # Track gate performance
                        self._update_gate_perf(gate_audit, outcome)

                        # Store raw record for GA
                        record = {
                            "symbol": symbol,
                            "direction": direction,
                            "gate_audit": gate_audit,
                            "outcome": outcome,
                            "rr_achieved": signal.get("rr_achieved", 0.0),
                            "session": gate_audit.get("G1_session", "unknown"),
                            "structure_quality": gate_audit.get("G2_quality", "unknown"),
                            "poi_type": gate_audit.get("G11_poi_type", "none"),
                            "narrative": gate_audit.get("G7_narrative", "unknown"),
                        }
                        self._signal_records.append(record)
                        # Cap in-memory records to avoid OOM during long backtests
                        if len(self._signal_records) > 50_000:
                            self._signal_records = self._signal_records[-50_000:]
        except Exception as exc:
            log.error("[Memory] record_bar error: %s", exc)

    def _update_gate_perf(self, gate_audit: dict, outcome: str) -> None:
        """Update gate win-rate counters. Must be called inside lock."""
        try:
            gp = self._data.setdefault("gate_performance", {})
            won = outcome in ("TP1_HIT", "TP2_HIT")

            # Structure quality
            struct_q = gate_audit.get("G2_quality", "")
            if struct_q:
                key = f"G2_structure_{struct_q}_winrate"
                self._update_winrate(gp, key, won)

            # Narrative
            narr = gate_audit.get("G7_narrative", "")
            if narr:
                key = f"G7_{narr}_winrate"
                self._update_winrate(gp, key, won)

            # POI type
            poi = gate_audit.get("G11_poi_type", "")
            if poi and poi != "none":
                key = f"G11_{poi}_winrate"
                self._update_winrate(gp, key, won)

            # Zone
            zone = gate_audit.get("G3_zone", "")
            if zone:
                key = f"G3_{zone}_winrate"
                self._update_winrate(gp, key, won)
        except Exception as exc:
            log.debug("[Memory] _update_gate_perf error: %s", exc)

    @staticmethod
    def _update_winrate(gp: dict, key: str, won: bool) -> None:
        """Rolling winrate update using exponential moving average (alpha=0.05)."""
        alpha = 0.05
        current = gp.get(key, 0.5)
        gp[key] = current * (1 - alpha) + (1.0 if won else 0.0) * alpha

    def record_live_signal(self, signal_dict: dict, outcome: Optional[str] = None) -> None:
        """Record a live dispatched signal. Outcome can be filled in later."""
        try:
            with self._lock:
                self._data["total_signals_seen"] = self._data.get("total_signals_seen", 0) + 1
                if outcome:
                    symbol = signal_dict.get("symbol", "")
                    direction = signal_dict.get("direction", "")
                    per_pair = self._data.setdefault("per_pair", {})
                    pair_stats = per_pair.setdefault(symbol, {
                        "total_bars": 0,
                        "signals": {
                            "BUY":  {"count": 0, "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0, "expired": 0},
                            "SELL": {"count": 0, "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0, "expired": 0},
                        },
                        "rr_samples": [],
                        "avg_rr_achieved": 0.0,
                    })
                    if direction in ("BUY", "SELL"):
                        sig_stats = pair_stats["signals"][direction]
                        sig_stats["count"] += 1
                        if outcome == "TP1_HIT":
                            sig_stats["tp1_hit"] += 1
                        elif outcome == "TP2_HIT":
                            sig_stats["tp1_hit"] += 1
                            sig_stats["tp2_hit"] += 1
                        elif outcome == "SL_HIT":
                            sig_stats["sl_hit"] += 1
                        elif outcome == "EXPIRED":
                            sig_stats["expired"] += 1
        except Exception as exc:
            log.error("[Memory] record_live_signal error: %s", exc)

    def record_live_signal_outcome(self, signal_dict: dict, outcome: str) -> None:
        """Update outcome for a previously dispatched live signal."""
        self.record_live_signal(signal_dict, outcome)

    def record_regime(self, regime: str, won: bool, rr: float) -> None:
        """Record a trade result under a market regime."""
        try:
            with self._lock:
                regimes = self._data.setdefault("market_regimes", {})
                entry = regimes.setdefault(regime, {
                    "win_rate": 0.5,
                    "avg_rr": 1.0,
                    "signal_count": 0,
                })
                n = entry["signal_count"]
                entry["signal_count"] = n + 1
                # Rolling average
                alpha = min(0.1, 1.0 / max(entry["signal_count"], 1))
                entry["win_rate"] = entry["win_rate"] * (1 - alpha) + (1.0 if won else 0.0) * alpha
                if rr > 0:
                    entry["avg_rr"] = entry["avg_rr"] * (1 - alpha) + rr * alpha
        except Exception as exc:
            log.error("[Memory] record_regime error: %s", exc)

    def append_log(self, event: dict) -> None:
        """Append an entry to the learning_log (capped at 200 entries)."""
        try:
            with self._lock:
                log_list = self._data.setdefault("learning_log", [])
                event["timestamp"] = datetime.now(timezone.utc).isoformat()
                log_list.append(event)
                if len(log_list) > 200:
                    self._data["learning_log"] = log_list[-200:]
        except Exception as exc:
            log.debug("[Memory] append_log error: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Q-Table (RL)
    # ──────────────────────────────────────────────────────────────────────────

    def get_q_table(self) -> dict:
        """Return a copy of the persisted Q-table."""
        with self._lock:
            return dict(self._data.get("q_table", {}))

    def set_q_table(self, q_table: dict) -> None:
        """Replace the persisted Q-table."""
        with self._lock:
            self._data["q_table"] = dict(q_table)

    # ──────────────────────────────────────────────────────────────────────────
    # Queries
    # ──────────────────────────────────────────────────────────────────────────

    def get_evolved_params(self) -> dict:
        """Return evolved strategy parameters, or empty dict if not yet evolved."""
        return self.evolved_parameters

    def get_pair_stats(self, symbol: str) -> dict:
        """Return per-pair statistics dict."""
        with self._lock:
            return dict(self._data.get("per_pair", {}).get(symbol, {}))

    def get_all_signal_records(self) -> list[dict]:
        """Return in-memory raw signal records for GA fitness evaluation."""
        return list(self._signal_records)

    def get_best_conditions(self) -> dict:
        """Return a summary of historically best-performing conditions."""
        try:
            with self._lock:
                gp = self._data.get("gate_performance", {})
                regimes = self._data.get("market_regimes", {})
                per_pair = self._data.get("per_pair", {})

            # Best pair by TP1 hit rate
            best_pair = None
            best_pair_rate = 0.0
            for sym, stats in per_pair.items():
                signals = stats.get("signals", {})
                total = sum(s.get("count", 0) for s in signals.values())
                tp1 = sum(s.get("tp1_hit", 0) for s in signals.values())
                if total > 10:
                    rate = tp1 / total
                    if rate > best_pair_rate:
                        best_pair_rate = rate
                        best_pair = sym

            # Best regime
            best_regime = max(regimes.items(), key=lambda x: x[1].get("win_rate", 0), default=(None, {}))

            return {
                "best_pair": best_pair,
                "best_pair_tp1_rate": round(best_pair_rate, 3),
                "best_regime": best_regime[0],
                "best_regime_win_rate": round(best_regime[1].get("win_rate", 0), 3),
                "top_gate_stats": {k: round(v, 3) for k, v in list(gp.items())[:10]},
            }
        except Exception as exc:
            log.error("[Memory] get_best_conditions error: %s", exc)
            return {}

    def get_summary(self) -> dict:
        """Return a high-level summary for reporting."""
        try:
            with self._lock:
                data = dict(self._data)

            total_tp1 = 0
            total_sigs = 0
            for sym_stats in data.get("per_pair", {}).values():
                for dir_stats in sym_stats.get("signals", {}).values():
                    total_sigs += dir_stats.get("count", 0)
                    total_tp1 += dir_stats.get("tp1_hit", 0)

            overall_win_rate = (total_tp1 / total_sigs) if total_sigs > 0 else 0.0
            evolved = data.get("evolved_parameters", {})
            regimes = data.get("market_regimes", {})

            return {
                "total_bars_processed": data.get("total_bars_processed", 0),
                "total_signals_seen": total_sigs,
                "overall_win_rate": round(overall_win_rate, 4),
                "evolution_generation": evolved.get("generation", 0),
                "evolution_fitness": evolved.get("fitness_score", 0.0),
                "active_regimes": len(regimes),
                "pairs_tracked": len(data.get("per_pair", {})),
                "last_updated": data.get("last_updated", ""),
            }
        except Exception as exc:
            log.error("[Memory] get_summary error: %s", exc)
            return {}

    # ──────────────────────────────────────────────────────────────────────────
    # Backtest progress
    # ──────────────────────────────────────────────────────────────────────────

    def set_backtest_progress(self, total: int, done: int, running: bool) -> None:
        """Update backtest progress counters (not persisted)."""
        self._backtest_total_pairs = total
        self._backtest_done_pairs = done
        self._backtest_running = running

    def get_backtest_progress(self) -> dict:
        """Return current backtest progress."""
        total = self._backtest_total_pairs
        done = self._backtest_done_pairs
        pct = (done / total * 100) if total > 0 else 100.0
        return {
            "running": self._backtest_running,
            "total_pairs": total,
            "done_pairs": done,
            "percent": round(pct, 1),
        }
