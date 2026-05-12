"""
data/candle_engine.py — Real-Time Deriv Candle Engine
======================================================
Single source of truth for all OHLC candle data.

Architecture:
  - Persistent Deriv WebSocket connection per symbol
  - Tick stream → OHLC aggregation (M1, M5, H1)
  - In-memory ring-buffer store (max 500 candles per tf per symbol)
  - Thread-safe reads for strategy engine + web API
  - Auto-reconnect with exponential backoff
  - Buffers ticks during reconnect — no gap data lost

Timeframe constants used everywhere:
    TF_M1  = "M1"
    TF_M5  = "M5"
    TF_H1  = "H1"
    TF_M15 = "M15"   (historical fetch only — built from M1 ticks)

Candle format (matches existing strategy contract):
    {
        "timestamp": <int unix epoch — start of candle bucket>,
        "open":      <float>,
        "high":      <float>,
        "low":       <float>,
        "close":     <float>,
        "volume":    0.0,        # Deriv tick stream carries no volume
    }

Web API format (TradingView Lightweight Charts):
    {
        "time":  <int unix epoch>,
        "open":  <float>,
        "high":  <float>,
        "low":   <float>,
        "close": <float>,
    }
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import websocket  # websocket-client

from core.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

from core.config import DERIV_APP_ID as _DERIV_APP_ID

_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={_DERIV_APP_ID}"
log.info("Deriv WebSocket URL: %s", _WS_URL)

# Timeframe bucket sizes in seconds
_TF_SECONDS: Dict[str, int] = {
    "M1":  60,
    "M5":  300,
    "M15": 900,
    "H1":  3600,
}

# All timeframes the engine builds from ticks
_LIVE_TIMEFRAMES = ("M1", "M5", "M15", "H1")

# Ring-buffer size per (symbol, timeframe)
_MAX_CANDLES = 500

# Historical seed: fetch this many candles on startup per timeframe
_SEED_COUNT = 1000  # Fetch up to 1000 historical candles on startup for full backfill

# WebSocket timing
_PING_INTERVAL  = 25      # seconds between keep-alive pings
_RECONNECT_BASE =  2.0    # seconds; doubles each attempt
_RECONNECT_MAX  = 60.0
_RECV_TIMEOUT   = 30
_CONNECT_TIMEOUT = 15

# Symbol map  internal → Deriv
_SYMBOL_MAP: Dict[str, str] = {
    # ── Forex majors (all confirmed on Deriv frxXXXYYY feed) ─────────────────
    "EUR/USD": "frxEURUSD",
    "GBP/USD": "frxGBPUSD",
    "USD/JPY": "frxUSDJPY",
    "AUD/USD": "frxAUDUSD",
    "USD/CHF": "frxUSDCHF",
    "USD/CAD": "frxUSDCAD",
    "NZD/USD": "frxNZDUSD",
    # ── Forex crosses (minors) ─────────────────────────────────────────────────
    "EUR/GBP": "frxEURGBP",
    "GBP/JPY": "frxGBPJPY",
    "EUR/JPY": "frxEURJPY",
    "AUD/JPY": "frxAUDJPY",
    "GBP/CAD": "frxGBPCAD",
    "EUR/CAD": "frxEURCAD",
    "EUR/CHF": "frxEURCHF",
    "CAD/JPY": "frxCADJPY",
    # ── No-slash alternate formats ────────────────────────────────────────────
    "EURUSD":  "frxEURUSD",
    "GBPUSD":  "frxGBPUSD",
    "USDJPY":  "frxUSDJPY",
    "AUDUSD":  "frxAUDUSD",
    "USDCHF":  "frxUSDCHF",
    "USDCAD":  "frxUSDCAD",
    "NZDUSD":  "frxNZDUSD",
    "EURGBP":  "frxEURGBP",
    "GBPJPY":  "frxGBPJPY",
    "EURJPY":  "frxEURJPY",
    "AUDJPY":  "frxAUDJPY",
    "GBPCAD":  "frxGBPCAD",
    "EURCAD":  "frxEURCAD",
    "EURCHF":  "frxEURCHF",
    "CADJPY":  "frxCADJPY",
    # ── Crypto (all available on Deriv cryXXXUSD synthetic feed) ─────────────────
    "BTC/USD": "cryBTCUSD",
    "BTCUSD":  "cryBTCUSD",
    "BTCUSDT": "cryBTCUSD",
    "ETH/USD": "cryETHUSD",
    "ETHUSD":  "cryETHUSD",
    "ETHUSDT": "cryETHUSD",
    "SOL/USD": "crySOLUSD",
    "SOLUSD":  "crySOLUSD",
    "SOLUSDT": "crySOLUSD",
    "BNB/USD": "cryBNBUSD",
    "BNBUSD":  "cryBNBUSD",
    "BNBUSDT": "cryBNBUSD",
    "XRP/USD": "cryXRPUSD",
    "XRPUSD":  "cryXRPUSD",
    "XRPUSDT": "cryXRPUSD",
    "ADA/USD": "cryADAUSD",
    "ADAUSD":  "cryADAUSD",
    "ADAUSDT": "cryADAUSD",
    "DOGE/USD": "cryDOGEUSD",
    "DOGEUSD":  "cryDOGEUSD",
    "DOGEUSDT": "cryDOGEUSD",
    "LTC/USD": "cryLTCUSD",
    "LTCUSD":  "cryLTCUSD",
    "LTCUSDT": "cryLTCUSD",
}

# Granularity map for historical seeding
_GRAN_MAP: Dict[str, int] = {
    "M1":  60,
    "M5":  300,
    "M15": 900,
    "H1":  3600,
}


# ── Candle bucket helpers ─────────────────────────────────────────────────────

def _bucket_start(epoch: int, tf_seconds: int) -> int:
    """Return the epoch timestamp of the candle bucket containing *epoch*."""
    return (epoch // tf_seconds) * tf_seconds


def _make_candle(epoch: int, price: float) -> dict:
    return {
        "timestamp": epoch,
        "open":  price,
        "high":  price,
        "low":   price,
        "close": price,
        "volume": 0.0,
    }


# ── CandleStore ───────────────────────────────────────────────────────────────

class CandleStore:
    """
    Thread-safe per-symbol candle ring-buffer.

    Supports:
      - Seeding with historical candles
      - Incremental tick updates (update current candle or open new one)
      - Snapshot reads for strategy + web API
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._lock = threading.RLock()

        # {tf: deque[candle_dict]}
        self._candles: Dict[str, deque] = {
            tf: deque(maxlen=_MAX_CANDLES) for tf in _LIVE_TIMEFRAMES
        }
        # {tf: current_open_candle | None}
        self._live: Dict[str, Optional[dict]] = {tf: None for tf in _LIVE_TIMEFRAMES}

    # ── Seeding ───────────────────────────────────────────────────────────────

    def seed(self, tf: str, candles: List[dict]) -> None:
        """Load historical candles (oldest→newest). Called once on startup."""
        if tf not in _LIVE_TIMEFRAMES:
            return
        with self._lock:
            buf = self._candles[tf]
            buf.clear()
            for c in candles:
                buf.append(c)
            # Prime live candle with the last historical close
            if candles:
                last = candles[-1]
                # Last historical candle becomes the live partial candle
                # (it might still be open if its bucket hasn't closed yet)
                self._live[tf] = dict(last)
        log.debug("[%s] Seeded %d %s candles", self.symbol, len(candles), tf)

    # ── Tick ingestion ─────────────────────────────────────────────────────────

    def on_tick(self, price: float, epoch: int) -> List[Tuple[str, dict]]:
        """
        Update all timeframes with a new tick.

        Returns list of (tf, closed_candle) for each candle that closed,
        so callers can broadcast to WebSocket subscribers.
        """
        if price <= 0 or not math.isfinite(price):
            return []

        closed: List[Tuple[str, dict]] = []

        with self._lock:
            for tf, tf_secs in _TF_SECONDS.items():
                if tf not in _LIVE_TIMEFRAMES:
                    continue

                bucket = _bucket_start(epoch, tf_secs)
                live = self._live[tf]

                if live is None:
                    # First tick ever for this tf
                    self._live[tf] = _make_candle(bucket, price)
                    continue

                if bucket > live["timestamp"]:
                    # New bucket → close current live candle, push to store
                    finished = dict(live)
                    self._candles[tf].append(finished)
                    closed.append((tf, finished))
                    # Open fresh candle for new bucket
                    self._live[tf] = _make_candle(bucket, price)
                elif bucket == live["timestamp"]:
                    # Same bucket → update OHLC
                    live["high"]  = max(live["high"],  price)
                    live["low"]   = min(live["low"],   price)
                    live["close"] = price
                # bucket < live["timestamp"] → late/duplicate tick; ignore

        return closed

    # ── Snapshot reads ─────────────────────────────────────────────────────────

    def get_candles(self, tf: str, limit: int = 200) -> List[dict]:
        """
        Return closed candles + current live partial candle.

        Format matches the existing strategy contract:
        [{timestamp, open, high, low, close, volume}, ...]  oldest→newest
        """
        if tf not in _LIVE_TIMEFRAMES:
            return []

        with self._lock:
            closed = list(self._candles[tf])
            live   = self._live[tf]

        result = closed
        if live is not None:
            result = closed + [dict(live)]

        return result[-limit:]

    def get_candles_tv(self, tf: str, limit: int = 200) -> List[dict]:
        """
        TradingView Lightweight Charts format:
        [{time, open, high, low, close}, ...]  oldest→newest
        """
        raw = self.get_candles(tf, limit)
        return [
            {
                "time":  c["timestamp"],
                "open":  c["open"],
                "high":  c["high"],
                "low":   c["low"],
                "close": c["close"],
            }
            for c in raw
        ]

    def latest_price(self) -> Optional[float]:
        """Return the most recent close price across all timeframes."""
        with self._lock:
            live = self._live.get("M1")
            if live:
                return live["close"]
        return None


# ── Global registry ───────────────────────────────────────────────────────────

_stores: Dict[str, CandleStore] = {}
_stores_lock = threading.RLock()


def _get_store(symbol: str) -> CandleStore:
    """Get or create a CandleStore for *symbol*."""
    with _stores_lock:
        if symbol not in _stores:
            _stores[symbol] = CandleStore(symbol)
        return _stores[symbol]


def get_candles(symbol: str, tf: str, limit: int = 200) -> List[dict]:
    """
    Public API: return OHLC candles in strategy format.
    Returns [] if symbol is not tracked (engine not started).
    """
    return _get_store(symbol).get_candles(tf, limit)


def get_candles_tv(symbol: str, tf: str, limit: int = 200) -> List[dict]:
    """Public API: return candles in TradingView format."""
    return _get_store(symbol).get_candles_tv(tf, limit)


def get_latest_price(symbol: str) -> Optional[float]:
    """Return latest tick price for symbol."""
    return _get_store(symbol).latest_price()


def list_symbols() -> List[str]:
    """Return all tracked symbols."""
    with _stores_lock:
        return list(_stores.keys())


# ── WebSocket subscribers (for /ws/candles) ──────────────────────────────────

_subscribers: List = []          # list of queue.Queue
_subscribers_lock = threading.Lock()


def subscribe() -> "queue.Queue":
    """
    Register a subscriber queue that receives candle update dicts:
    {"symbol": str, "tf": str, "candle": {time,open,high,low,close}}
    """
    import queue
    q: queue.Queue = queue.Queue(maxsize=500)
    with _subscribers_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q) -> None:
    with _subscribers_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(symbol: str, tf: str, candle: dict) -> None:
    tv_candle = {
        "time":  candle["timestamp"],
        "open":  candle["open"],
        "high":  candle["high"],
        "low":   candle["low"],
        "close": candle["close"],
    }
    msg = {"symbol": symbol, "tf": tf, "candle": tv_candle}
    with _subscribers_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# ── Historical seed fetcher ───────────────────────────────────────────────────

def _fetch_historical(deriv_symbol: str, tf: str, count: int = _SEED_COUNT) -> Optional[List[dict]]:
    """
    Fetch historical candles via Deriv ticks_history (one-shot WS call).
    Returns list of strategy-format candles or None.
    """
    gran = _GRAN_MAP.get(tf)
    if gran is None:
        return None

    ws = None
    try:
        ws = websocket.WebSocket()
        ws.settimeout(_RECV_TIMEOUT)
        ws.connect(_WS_URL, timeout=_CONNECT_TIMEOUT)

        # Optional auth
        try:
            from core.config import DERIV_API_TOKEN
            token = DERIV_API_TOKEN or ""
        except Exception:
            token = ""

        if token:
            ws.send(json.dumps({"authorize": token}))
            ws.recv()  # ignore auth response for seeding

        ws.send(json.dumps({
            "ticks_history": deriv_symbol,
            "style":         "candles",
            "granularity":   gran,
            "count":         count,
            "end":           "latest",
        }))
        raw = ws.recv()
        data = json.loads(raw)

        if "error" in data:
            log.warning("Deriv historical error for %s %s: %s", deriv_symbol, tf, data["error"])
            return None

        raw_candles = data.get("candles", [])
        if not raw_candles:
            return None

        result = []
        for c in raw_candles:
            try:
                result.append({
                    "timestamp": int(c["epoch"]),
                    "open":   float(c["open"]),
                    "high":   float(c["high"]),
                    "low":    float(c["low"]),
                    "close":  float(c["close"]),
                    "volume": 0.0,
                })
            except (KeyError, ValueError, TypeError):
                continue

        return result if result else None

    except Exception as exc:
        log.warning("Historical fetch error for %s %s: %s", deriv_symbol, tf, exc)
        return None
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


# ── Per-symbol live connection ────────────────────────────────────────────────

class _SymbolFeed(threading.Thread):
    """
    Manages one persistent WebSocket connection for one symbol.
    Feeds ticks into CandleStore.  Auto-reconnects with backoff.
    """

    def __init__(self, symbol: str, deriv_symbol: str) -> None:
        super().__init__(name=f"Feed-{symbol}", daemon=True)
        self.symbol       = symbol
        self.deriv_symbol = deriv_symbol
        self._stop        = threading.Event()
        self._store       = _get_store(symbol)
        self._tick_buffer: List[Tuple[float, int]] = []  # buffered during reconnect

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # ── Step 1: Seed with historical candles ──────────────────────────────
        for tf in _LIVE_TIMEFRAMES:
            try:
                log.info("[%s] Seeding %s candles...", self.symbol, tf)
                hist = _fetch_historical(self.deriv_symbol, tf)
                if hist:
                    self._store.seed(tf, hist)
                else:
                    log.warning("[%s] No historical %s candles returned", self.symbol, tf)
            except Exception as exc:
                log.warning("[%s] Seed error for %s: %s", self.symbol, tf, exc)

        # ── Step 2: Live tick subscription ────────────────────────────────────
        backoff = _RECONNECT_BASE
        while not self._stop.is_set():
            try:
                self._connect_and_stream()
                backoff = _RECONNECT_BASE  # reset on clean exit
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("[%s] Feed error (%s) — reconnecting in %.0fs", self.symbol, exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

    def _connect_and_stream(self) -> None:
        ws = websocket.WebSocket()
        ws.settimeout(_RECV_TIMEOUT)
        ws.connect(_WS_URL, timeout=_CONNECT_TIMEOUT)

        # Auth if token provided
        try:
            from core.config import DERIV_API_TOKEN
            token = DERIV_API_TOKEN or ""
        except Exception:
            token = ""

        if token:
            ws.send(json.dumps({"authorize": token}))
            auth_resp = json.loads(ws.recv())
            if "error" in auth_resp:
                log.warning("[%s] Auth error: %s", self.symbol, auth_resp["error"].get("message"))

        # Subscribe to tick stream
        ws.send(json.dumps({
            "ticks":      self.deriv_symbol,
            "subscribe":  1,
        }))

        log.info("[%s] Live tick stream connected", self.symbol)

        # Flush buffered ticks from previous disconnect
        for price, epoch in self._tick_buffer:
            self._store.on_tick(price, epoch)
        self._tick_buffer.clear()

        last_ping = time.time()

        while not self._stop.is_set():
            # Keep-alive ping
            if time.time() - last_ping > _PING_INTERVAL:
                try:
                    ws.send(json.dumps({"ping": 1}))
                except Exception:
                    break
                last_ping = time.time()

            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                # Normal on quiet markets; send ping
                try:
                    ws.send(json.dumps({"ping": 1}))
                    last_ping = time.time()
                except Exception:
                    break
                continue
            except Exception as exc:
                log.warning("[%s] recv error: %s", self.symbol, exc)
                break

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            msg_type = msg.get("msg_type")

            if msg_type == "tick":
                tick = msg.get("tick", {})
                try:
                    price = float(tick["quote"])
                    epoch = int(tick["epoch"])
                except (KeyError, ValueError, TypeError):
                    continue

                closed = self._store.on_tick(price, epoch)
                for tf, candle in closed:
                    _broadcast(self.symbol, tf, candle)

            elif msg_type == "pong":
                pass  # keep-alive confirmed

            elif "error" in msg:
                log.warning("[%s] WS error msg: %s", self.symbol, msg["error"])

        try:
            ws.close()
        except Exception:
            pass


# ── Engine startup ────────────────────────────────────────────────────────────

_feeds: Dict[str, _SymbolFeed] = {}
_engine_started = False
_engine_lock = threading.Lock()


def start(symbols: Optional[List[str]] = None) -> None:
    """
    Start the candle engine for all *symbols*.

    If symbols is None, defaults to the FOREX_PAIRS from config.
    Call once from app.py before starting the trading engine.
    """
    global _engine_started
    with _engine_lock:
        if _engine_started:
            log.warning("CandleEngine already started — ignoring duplicate start()")
            return
        _engine_started = True

    if symbols is None:
        try:
            from core.config import FOREX_PAIRS, CRYPTO_PAIRS
            symbols = FOREX_PAIRS + CRYPTO_PAIRS
        except Exception:
            symbols = ["EUR/USD", "GBP/USD", "USD/JPY"]

    log.info("CandleEngine starting for symbols: %s", symbols)

    for sym in symbols:
        key = sym.upper().strip().replace("_", "/")
        deriv_sym = _SYMBOL_MAP.get(key) or _SYMBOL_MAP.get(sym.upper().strip())
        if deriv_sym is None:
            log.warning("CandleEngine: no Deriv symbol for %r — skipping", sym)
            continue

        # Ensure store exists
        _get_store(sym)

        feed = _SymbolFeed(sym, deriv_sym)
        feed.start()
        _feeds[sym] = feed
        log.info("CandleEngine: started feed for %s (%s)", sym, deriv_sym)

    log.info("CandleEngine: all feeds launched")


def stop() -> None:
    """Gracefully stop all feeds."""
    for feed in _feeds.values():
        feed.stop()
    log.info("CandleEngine: all feeds stopped")
  
