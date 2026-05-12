"""
trading/discipline.py — Signal rate limiting (SOFTENED + DROUGHT-AWARE)
=========================================================================
CHANGES IN THIS VERSION:
  - SYMBOL_COOLDOWN_MINUTES reduced from 60 → 30 (more signals per pair)
  - Drought detection: if a symbol hasn't fired in DROUGHT_HOURS (72h = 3 days),
    cooldown is halved so a good setup can still get through
  - MAX_TRADES_PER_HOUR / MAX_TRADES_PER_DAY limits remain from config
  - All original hardening retained (atomic writes, thread-safety, fail-open)
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from threading import Lock

from core.config import MAX_TRADES_PER_HOUR, MAX_TRADES_PER_DAY
from core.logger import get_logger
from core.utils import atomic_write_json, load_json_safe

log = get_logger(__name__)

_TRADES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trades.json")


def _parse_dt(s: str) -> datetime | None:
    """Parse an ISO datetime string safely. Returns None on error."""
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class Discipline:
    """
    Enforces per-symbol cooldowns and global hourly/daily trade limits.
    Thread-safe. Never crashes callers.
    """

    # Base cooldown between signals on the same pair.
    # Reduced 60 → 30 so more valid setups get through during active markets.
    SYMBOL_COOLDOWN_MINUTES: int = 30

    # If a pair has been silent for this many hours it enters "drought mode":
    # cooldown is halved so a good setup after a long dry spell fires immediately.
    DROUGHT_HOURS: int = 72   # 3 days

    def __init__(self) -> None:
        self._lock = Lock()
        self._signal_times: dict[str, list[datetime]] = defaultdict(list)
        self._all_times: list[datetime] = []
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        data = load_json_safe(_TRADES_FILE, default={})
        try:
            for sym, times in data.get("symbol_times", {}).items():
                parsed = [dt for t in times if (dt := _parse_dt(t)) is not None]
                self._signal_times[sym] = parsed

            self._all_times = [
                dt for t in data.get("all_times", [])
                if (dt := _parse_dt(t)) is not None
            ]
        except Exception as exc:
            log.warning("Discipline._load: error parsing trades.json: %s", exc)
            self._signal_times = defaultdict(list)
            self._all_times = []

    def _save(self) -> None:
        """Atomic write via temp-file + rename."""
        try:
            os.makedirs(os.path.dirname(_TRADES_FILE), exist_ok=True)
        except OSError:
            pass

        try:
            data = {
                "symbol_times": {
                    sym: [t.isoformat() for t in times]
                    for sym, times in self._signal_times.items()
                },
                "all_times": [t.isoformat() for t in self._all_times],
            }
            success = atomic_write_json(_TRADES_FILE, data)
            if not success:
                log.error("Discipline._save: atomic write failed for %s", _TRADES_FILE)
        except Exception as exc:
            log.error("Discipline._save: unexpected error: %s", exc)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _prune(self) -> None:
        """Remove timestamps older than 24 h."""
        try:
            cutoff = self._now() - timedelta(hours=24)
            self._all_times = [t for t in self._all_times if isinstance(t, datetime) and t > cutoff]
            for sym in list(self._signal_times):
                self._signal_times[sym] = [
                    t for t in self._signal_times[sym]
                    if isinstance(t, datetime) and t > cutoff
                ]
        except Exception as exc:
            log.warning("Discipline._prune error: %s", exc)

    # ── Public API ─────────────────────────────────────────────────────────────

    def can_signal(self, symbol: str) -> tuple[bool, str]:
        """
        Return (allowed, reason).
        Returns (True, "OK") on unexpected internal error — engine safety net.
        """
        try:
            with self._lock:
                self._prune()
                now = self._now()

                # Symbol cooldown (drought-aware)
                sym_times = self._signal_times.get(symbol, [])
                if sym_times:
                    last = max(sym_times)
                    elapsed_min = (now - last).total_seconds() / 60
                    elapsed_hours = elapsed_min / 60

                    # Drought mode: if last signal was > DROUGHT_HOURS ago,
                    # halve the cooldown so a fresh setup gets through faster.
                    drought = elapsed_hours >= self.DROUGHT_HOURS
                    effective_cooldown = (
                        self.SYMBOL_COOLDOWN_MINUTES // 2 if drought
                        else self.SYMBOL_COOLDOWN_MINUTES
                    )
                    if drought:
                        log.info(
                            "[%s] Drought mode active (%.0fh silent) — cooldown reduced to %dm",
                            symbol, elapsed_hours, effective_cooldown,
                        )

                    if elapsed_min < effective_cooldown:
                        remaining = int(effective_cooldown - elapsed_min)
                        return False, f"Symbol cooldown: {remaining}m remaining for {symbol}"

                # Hourly limit
                hour_cutoff = now - timedelta(hours=1)
                hourly_count = sum(1 for t in self._all_times if t > hour_cutoff)
                if hourly_count >= MAX_TRADES_PER_HOUR:
                    return False, f"Hourly limit reached ({MAX_TRADES_PER_HOUR}/h)"

                # Daily limit
                day_cutoff = now - timedelta(hours=24)
                daily_count = sum(1 for t in self._all_times if t > day_cutoff)
                if daily_count >= MAX_TRADES_PER_DAY:
                    return False, f"Daily limit reached ({MAX_TRADES_PER_DAY}/d)"

                return True, "OK"
        except Exception as exc:
            log.error("Discipline.can_signal unexpected error for %s: %s", symbol, exc)
            return True, "OK"  # Fail open — let engine continue

    def record_signal(self, symbol: str) -> None:
        """Record that a signal was sent for this symbol."""
        try:
            with self._lock:
                now = self._now()
                self._signal_times[symbol].append(now)
                self._all_times.append(now)
                self._save()
                log.debug(
                    "Signal recorded for %s | total today: %d",
                    symbol, len(self._all_times),
                )
        except Exception as exc:
            log.error("Discipline.record_signal failed for %s: %s", symbol, exc)
