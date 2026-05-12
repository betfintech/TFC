"""
learning/backtester.py — Historical Backtesting Engine
=======================================================
Fetches years of candle history for all pairs, replays bar-by-bar,
runs the full 12-gate SMC pipeline, and records outcomes to MemoryStore.

Runs in a background thread and NEVER blocks the trading engine.

Data sources:
  - Crypto: Binance REST API (klines)
  - Forex:  Deriv WebSocket (ticks_history)
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from core.config import CRYPTO_PAIRS, FOREX_PAIRS, BINANCE_BASE_URL
from core.logger import get_logger
from trading.strategy import generate_signal_with_audit, Signal

log = get_logger(__name__)

# ── Minimum candle windows for signal generation ────────────────────────────────
_MIN_H1_BARS  = 30
_MIN_M15_BARS = 60

# ── Outcome scan window (bars) ──────────────────────────────────────────────────
_OUTCOME_BARS = 48

# ── Deriv symbol map (subset from candle_engine) ───────────────────────────────
_DERIV_FOREX_MAP: dict[str, str] = {
    "EUR/USD": "frxEURUSD",
    "GBP/USD": "frxGBPUSD",
    "USD/JPY": "frxUSDJPY",
    "AUD/USD": "frxAUDUSD",
    "USD/CAD": "frxUSDCAD",
    "USD/CHF": "frxUSDCHF",
    "NZD/USD": "frxNZDUSD",
    "GBP/JPY": "frxGBPJPY",
    "EUR/JPY": "frxEURJPY",
    "AUD/JPY": "frxAUDJPY",
    "EUR/GBP": "frxEURGBP",
    "GBP/CAD": "frxGBPCAD",
    "EUR/CAD": "frxEURCAD",
    "EUR/CHF": "frxEURCHF",
    "CAD/JPY": "frxCADJPY",
}


class HistoricalBacktester:
    """
    Fetches max available historical candles, replays bar-by-bar,
    and records outcomes + gate audits to MemoryStore.
    """

    def __init__(self, memory) -> None:
        self._memory = memory

    # ──────────────────────────────────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────────────────────────────────

    def run_all(self) -> None:
        """
        Run backtest for all configured pairs.
        Crypto fetched from Binance, Forex from Deriv.
        Updates memory.set_backtest_progress() throughout.
        """
        all_pairs = CRYPTO_PAIRS + FOREX_PAIRS
        self._memory.set_backtest_progress(len(all_pairs), 0, True)
        log.info("[Backtester] Starting — %d pairs", len(all_pairs))

        done = 0
        for symbol in all_pairs:
            try:
                self._run_pair(symbol)
            except Exception as exc:
                log.error("[Backtester] Error processing %s: %s", symbol, exc)
            done += 1
            self._memory.set_backtest_progress(len(all_pairs), done, True)
            # Save periodically
            if done % 5 == 0:
                self._memory.save()

        self._memory.set_backtest_progress(len(all_pairs), done, False)
        self._memory.save()
        log.info("[Backtester] Complete — %d bars processed", self._memory.total_bars_processed)
        self._memory.append_log({
            "event": "backtest_complete",
            "pairs_processed": done,
            "bars_processed": self._memory.total_bars_processed,
        })

    # ──────────────────────────────────────────────────────────────────────────
    # Per-pair
    # ──────────────────────────────────────────────────────────────────────────

    def _run_pair(self, symbol: str) -> None:
        """Fetch candles and run bar-by-bar replay for one symbol."""
        is_crypto = "/" not in symbol
        mtype = "crypto" if is_crypto else "forex"

        log.info("[Backtester] Fetching H1+M15 history for %s (%s)", symbol, mtype)

        if is_crypto:
            h1_candles  = self._fetch_binance(symbol, "1h",  limit_pages=8)
            m15_candles = self._fetch_binance(symbol, "15m", limit_pages=8)
        else:
            h1_candles  = self._fetch_deriv(symbol, granularity=3600,  count=5000)
            m15_candles = self._fetch_deriv(symbol, granularity=900,   count=5000)

        if not h1_candles or not m15_candles:
            log.warning("[Backtester] No candle data for %s — skipping", symbol)
            return

        log.info(
            "[Backtester] %s: %d H1 bars, %d M15 bars — starting replay",
            symbol, len(h1_candles), len(m15_candles),
        )
        self.replay(symbol, h1_candles, m15_candles, mtype)

    def replay(
        self,
        symbol: str,
        h1_candles: list[dict],
        m15_candles: list[dict],
        market_type: str,
    ) -> None:
        """
        Walk forward bar-by-bar, run strategy pipeline at each step,
        simulate outcome, and record to memory.
        """
        pending_signals: list[dict] = []  # signals awaiting outcome

        for i in range(_MIN_H1_BARS, len(h1_candles)):
            h1_window = h1_candles[:i + 1]

            # Align M15 to same timestamp cutoff
            cutoff = h1_candles[i].get("timestamp", 0)
            m15_window = [c for c in m15_candles if c.get("timestamp", 0) <= cutoff]

            if len(m15_window) < _MIN_M15_BARS:
                continue

            bar_data = h1_candles[i]

            # Check outcomes for pending signals at this bar
            resolved = []
            for pending in pending_signals:
                outcome = self._check_outcome_at_bar(
                    pending, h1_candles[pending["bar_index"]:i + 1]
                )
                if outcome:
                    self._memory.record_bar(
                        symbol, bar_data, pending["gate_audit"], pending, outcome
                    )
                    resolved.append(pending)
                elif i - pending["bar_index"] >= _OUTCOME_BARS:
                    # Expired
                    self._memory.record_bar(
                        symbol, bar_data, pending["gate_audit"], pending, "EXPIRED"
                    )
                    resolved.append(pending)

            for r in resolved:
                pending_signals.remove(r)

            # Generate signal
            try:
                signal, gate_audit = generate_signal_with_audit(
                    symbol, h1_window[-100:], m15_window[-100:], market_type
                )
            except Exception as exc:
                log.debug("[Backtester] strategy error at bar %d for %s: %s", i, symbol, exc)
                # Record bar without signal
                self._memory.record_bar(symbol, bar_data, {}, None, None)
                continue

            if signal.direction in ("BUY", "SELL"):
                rr_achieved = 0.0
                pending_signals.append({
                    "direction": signal.direction,
                    "entry": signal.entry,
                    "stop_loss": signal.stop_loss,
                    "tp1": signal.tp1,
                    "tp2": signal.tp2,
                    "bar_index": i,
                    "gate_audit": gate_audit,
                    "rr_achieved": rr_achieved,
                })
            else:
                self._memory.record_bar(symbol, bar_data, gate_audit, None, None)

        # Expire any remaining pending signals
        for pending in pending_signals:
            self._memory.record_bar(symbol, {}, pending["gate_audit"], pending, "EXPIRED")

    @staticmethod
    def _check_outcome_at_bar(pending: dict, forward_bars: list[dict]) -> Optional[str]:
        """
        Check if TP1, TP2, or SL was hit in the forward_bars slice.
        Returns outcome string or None if undecided.
        """
        direction = pending["direction"]
        entry     = pending["entry"]
        sl        = pending["stop_loss"]
        tp1       = pending["tp1"]
        tp2       = pending["tp2"]

        for bar in forward_bars[1:]:  # skip the signal bar itself
            high  = bar.get("high",  0.0)
            low   = bar.get("low",   0.0)
            close = bar.get("close", 0.0)
            rr = 0.0

            if direction == "BUY":
                if low <= sl:
                    return "SL_HIT"
                if high >= tp2 and tp2 > 0:
                    risk = entry - sl
                    rr   = (tp2 - entry) / risk if risk > 0 else 0.0
                    pending["rr_achieved"] = rr
                    return "TP2_HIT"
                if high >= tp1:
                    risk = entry - sl
                    rr   = (tp1 - entry) / risk if risk > 0 else 0.0
                    pending["rr_achieved"] = rr
                    return "TP1_HIT"
            else:  # SELL
                if high >= sl:
                    return "SL_HIT"
                if low <= tp2 and tp2 > 0:
                    risk = sl - entry
                    rr   = (entry - tp2) / risk if risk > 0 else 0.0
                    pending["rr_achieved"] = rr
                    return "TP2_HIT"
                if low <= tp1:
                    risk = sl - entry
                    rr   = (entry - tp1) / risk if risk > 0 else 0.0
                    pending["rr_achieved"] = rr
                    return "TP1_HIT"
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Data fetchers
    # ──────────────────────────────────────────────────────────────────────────

    def _fetch_binance(
        self, symbol: str, interval: str, limit_pages: int = 8
    ) -> list[dict]:
        """
        Fetch historical klines from Binance REST API.
        Paginates backward to collect up to limit_pages * 1000 candles.
        """
        url = f"{BINANCE_BASE_URL}/api/v3/klines"
        all_candles: list[dict] = []
        end_time: Optional[int] = None
        fetch_start = time.time()
        MAX_FETCH_SECS = 60  # never spend more than 60s fetching one symbol

        for page in range(limit_pages):
            if time.time() - fetch_start > MAX_FETCH_SECS:
                log.debug("[Backtester] Binance fetch timeout for %s after %ds", symbol, MAX_FETCH_SECS)
                break
            params: dict = {"symbol": symbol, "interval": interval, "limit": 1000}
            if end_time:
                params["endTime"] = end_time

            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                raw = resp.json()
            except Exception as exc:
                log.warning("[Backtester] Binance fetch error for %s: %s", symbol, exc)
                break

            if not raw:
                break

            candles = [
                {
                    "timestamp": row[0] // 1000,  # ms → s
                    "open":  float(row[1]),
                    "high":  float(row[2]),
                    "low":   float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
                for row in raw
            ]

            all_candles = candles + all_candles
            end_time = raw[0][0] - 1  # go back further
            time.sleep(0.2)  # rate limit courtesy

        return sorted(all_candles, key=lambda c: c["timestamp"])

    def _fetch_deriv(
        self, symbol: str, granularity: int = 3600, count: int = 5000
    ) -> list[dict]:
        """
        Fetch historical candles from Deriv WebSocket API.
        Uses ticks_history request with adjustable granularity.
        """
        deriv_sym = _DERIV_FOREX_MAP.get(symbol)
        if not deriv_sym:
            log.warning("[Backtester] No Deriv symbol mapping for %s", symbol)
            return []

        try:
            import websocket
            from core.config import DERIV_APP_ID
        except ImportError:
            log.warning("[Backtester] websocket-client not available for Deriv fetch")
            return []

        candles: list[dict] = []
        done_event = threading.Event()

        ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

        def on_message(ws, message: str) -> None:
            try:
                data = json.loads(message)
                if "candles" in data:
                    for c in data["candles"]:
                        candles.append({
                            "timestamp": int(c["epoch"]),
                            "open":  float(c["open"]),
                            "high":  float(c["high"]),
                            "low":   float(c["low"]),
                            "close": float(c["close"]),
                            "volume": 0.0,
                        })
                    done_event.set()
                    ws.close()
                elif "error" in data:
                    log.warning("[Backtester] Deriv error for %s: %s", symbol, data["error"])
                    done_event.set()
                    ws.close()
            except Exception as exc:
                log.debug("[Backtester] Deriv parse error: %s", exc)
                done_event.set()

        def on_error(ws, error) -> None:
            log.debug("[Backtester] Deriv WS error for %s: %s", symbol, error)
            done_event.set()

        def on_open(ws) -> None:
            request = {
                "ticks_history": deriv_sym,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "granularity": granularity,
                "style": "candles",
            }
            ws.send(json.dumps(request))

        try:
            ws_app = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
            )
            t = threading.Thread(
                target=ws_app.run_forever, kwargs={"ping_interval": 20}, daemon=True
            )
            t.start()
            done_event.wait(timeout=30)
            # Ensure WebSocket is closed even if we timed out waiting
            try:
                ws_app.close()
            except Exception:
                pass
        except Exception as exc:
            log.warning("[Backtester] Deriv WS setup failed for %s: %s", symbol, exc)

        return sorted(candles, key=lambda c: c["timestamp"])
