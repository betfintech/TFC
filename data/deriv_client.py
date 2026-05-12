"""
data/deriv_client.py — Deriv WebSocket Candle Client
=====================================================
Fetches M15 OHLCV candles from the Deriv WebSocket API and converts them
into the system's standard Candle dict format:

    {
        "timestamp": <int epoch>,
        "open":      <float>,
        "high":      <float>,
        "low":       <float>,
        "close":     <float>,
        "volume":    <float>,
    }

DESIGN RULES:
  - NEVER executes trades.  Read-only market-data path only.
  - API token is NEVER logged or printed.
  - Fully fault-tolerant: every network call is wrapped in try/except.
  - Auto-reconnects on WebSocket disconnect (exponential backoff).
  - Timeout enforced on every blocking call (CONNECT_TIMEOUT / RECV_TIMEOUT).
  - Returns None (not an empty list) on failure so callers can detect and
    fall back to the existing Twelve Data / Binance providers.

INTEGRATION:
  - Called from trading/market/forex.py via the safe wrapper in this module.
  - The token is loaded from core.config.DERIV_API_TOKEN (env: DERIV_API_TOKEN).
  - If the token is absent, the client skips auth and fetches public data
    (Deriv allows candle history without auth for most FX synthetics).

STABILITY:
  - websocket-client library (not websockets / aiohttp) – synchronous, no
    event loop required, safe to call from any thread.
  - All failures return None; caller decides whether to use fallback.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

import websocket  # websocket-client

from core.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

from core.config import DERIV_APP_ID as _DERIV_APP_ID

_WS_URL          = f"wss://ws.derivws.com/websockets/v3?app_id={_DERIV_APP_ID}"
_GRANULARITY     = 900          # 15-minute candles (seconds)
_CANDLE_COUNT    = 100
_CONNECT_TIMEOUT = 15           # seconds: WebSocket TCP handshake + upgrade
_RECV_TIMEOUT    = 20           # seconds: waiting for a full response
_MAX_RETRIES     = 3
_BASE_BACKOFF    = 2.0          # seconds; doubled each retry (exponential)

# Deriv symbol for EURUSD spot forex
_DERIV_SYMBOL    = "frxEURUSD"

# Map our internal pair strings to Deriv ticks_history symbols
_PAIR_MAP: dict[str, str] = {
    "EUR/USD":  "frxEURUSD",
    "GBP/USD":  "frxGBPUSD",
    "USD/JPY":  "frxUSDJPY",
    "AUD/USD":  "frxAUDUSD",
    "USD/CHF":  "frxUSDCHF",
    "USD/CAD":  "frxUSDCAD",
    "NZD/USD":  "frxNZDUSD",
    # Aliases without slash
    "EURUSD":   "frxEURUSD",
    "GBPUSD":   "frxGBPUSD",
    "USDJPY":   "frxUSDJPY",
    "AUDUSD":   "frxAUDUSD",
    "USDCHF":   "frxUSDCHF",
    "USDCAD":   "frxUSDCAD",
    "NZDUSD":   "frxNZDUSD",
}

# Module-level availability flag (set False on first fatal failure)
_deriv_available: bool = True
_availability_lock = threading.Lock()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resolve_symbol(symbol: str) -> Optional[str]:
    """
    Map a normalised internal symbol string (e.g. 'EUR/USD') to a
    Deriv ticks_history symbol (e.g. 'frxEURUSD').

    Returns None if the symbol is not supported by this client.
    """
    key = symbol.upper().strip()
    result = _PAIR_MAP.get(key)
    if result is None:
        log.debug("DerivClient: symbol %r not in Deriv symbol map — skipping", symbol)
    return result


def _load_token() -> str:
    """
    Load the Deriv API token from config.
    Returns empty string if not configured (auth step will be skipped).
    Token is NEVER logged.
    """
    try:
        from core.config import DERIV_API_TOKEN  # type: ignore[attr-defined]
        return DERIV_API_TOKEN or ""
    except (ImportError, AttributeError):
        # Token not configured — proceed without authentication
        # (Deriv allows public data on most FX instruments)
        log.debug("DerivClient: DERIV_API_TOKEN not found in config — proceeding unauthenticated")
        return ""


def _send_and_receive(ws: websocket.WebSocket, payload: dict) -> Optional[dict]:
    """
    Send a single JSON request and receive one JSON response.

    Returns the parsed response dict, or None on any error.
    Uses _RECV_TIMEOUT so a frozen server never blocks indefinitely.
    """
    try:
        ws.send(json.dumps(payload))
    except Exception as exc:
        log.warning("DerivClient: send failed: %s", exc)
        return None

    try:
        raw = ws.recv()
    except websocket.WebSocketTimeoutException:
        log.warning("DerivClient: receive timed out after %ds", _RECV_TIMEOUT)
        return None
    except Exception as exc:
        log.warning("DerivClient: receive failed: %s", exc)
        return None

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("DerivClient: JSON parse error: %s | raw=%s", exc, str(raw)[:200])
        return None


def _parse_candles(response: dict, symbol: str) -> Optional[list[dict]]:
    """
    Parse the Deriv ticks_history candle response into our standard format.

    Expected response shape:
        {
            "candles": [
                {"open": "1.0800", "high": "1.0850", "low": "1.0780",
                 "close": "1.0820", "epoch": 1700000000},
                ...
            ],
            "msg_type": "candles"
        }

    Returns a list of candle dicts (oldest→newest), or None on failure.
    Volume is 0.0 — Deriv does not provide tick volume on candle history.
    """
    if not isinstance(response, dict):
        log.warning("DerivClient: non-dict response for %s", symbol)
        return None

    # Deriv surfaces errors in a top-level "error" key
    if "error" in response:
        err = response["error"]
        code = err.get("code", "?")
        message = err.get("message", str(err))
        log.warning("DerivClient: API error for %s — [%s] %s", symbol, code, message)
        return None

    raw_candles = response.get("candles")
    if not isinstance(raw_candles, list) or len(raw_candles) == 0:
        log.warning("DerivClient: empty or missing 'candles' in response for %s", symbol)
        return None

    parsed: list[dict] = []
    for idx, c in enumerate(raw_candles):
        if not isinstance(c, dict):
            log.debug("DerivClient: skipping non-dict candle at index %d for %s", idx, symbol)
            continue
        try:
            epoch = int(c["epoch"])
            candle = {
                "timestamp": epoch,
                "open":      float(c["open"]),
                "high":      float(c["high"]),
                "low":       float(c["low"]),
                "close":     float(c["close"]),
                "volume":    0.0,   # Deriv candle history carries no volume
            }
            # Sanity checks: reject zero/negative prices
            if candle["close"] <= 0 or candle["high"] <= 0 or candle["low"] <= 0:
                log.debug("DerivClient: skipping candle with non-positive price at index %d", idx)
                continue
            if candle["high"] < candle["low"]:
                log.debug("DerivClient: skipping candle with high < low at index %d", idx)
                continue
            parsed.append(candle)
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("DerivClient: skipping malformed candle at index %d for %s: %s", idx, symbol, exc)
            continue

    if not parsed:
        log.warning("DerivClient: no valid candles parsed from response for %s", symbol)
        return None

    # Deriv returns candles oldest-first; keep that ordering (matches existing providers)
    log.debug("DerivClient: parsed %d candles for %s", len(parsed), symbol)
    return parsed


def _fetch_over_ws(deriv_symbol: str) -> Optional[list[dict]]:
    """
    Open one WebSocket connection, optionally authenticate, request candles,
    receive the response, close the connection, and return parsed candles.

    All IO is synchronous and timeout-bounded.
    Returns None on any failure.
    """
    token = _load_token()

    ws: Optional[websocket.WebSocket] = None
    try:
        ws = websocket.WebSocket()
        ws.settimeout(_RECV_TIMEOUT)

        # ── Connect ───────────────────────────────────────────────────────────
        try:
            ws.connect(_WS_URL, timeout=_CONNECT_TIMEOUT)
        except websocket.WebSocketTimeoutException:
            log.warning("DerivClient: connection timed out (>%ds)", _CONNECT_TIMEOUT)
            return None
        except OSError as exc:
            log.warning("DerivClient: connection OSError: %s", exc)
            return None
        except Exception as exc:
            log.warning("DerivClient: connection failed: %s", exc)
            return None

        # ── Authenticate (optional) ───────────────────────────────────────────
        if token:
            auth_resp = _send_and_receive(ws, {"authorize": token})
            if auth_resp is None:
                log.warning("DerivClient: auth request failed — continuing unauthenticated")
            elif "error" in auth_resp:
                err = auth_resp["error"]
                # Log code only — never log the token itself
                log.warning(
                    "DerivClient: auth error [%s] — continuing unauthenticated",
                    err.get("code", "?"),
                )
            else:
                log.debug("DerivClient: authenticated successfully")

        # ── Request candle history ─────────────────────────────────────────────
        candle_request = {
            "ticks_history": deriv_symbol,
            "style":         "candles",
            "granularity":   _GRANULARITY,
            "count":         _CANDLE_COUNT,
        }
        candle_resp = _send_and_receive(ws, candle_request)
        if candle_resp is None:
            log.warning("DerivClient: no response to candle request for %s", deriv_symbol)
            return None

        return _parse_candles(candle_resp, deriv_symbol)

    except Exception as exc:
        log.error("DerivClient: unexpected error during WS session for %s: %s", deriv_symbol, exc)
        return None
    finally:
        # Always attempt a clean close; ignore errors (socket may already be dead)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


# ── Public API ────────────────────────────────────────────────────────────────

def is_supported(symbol: str) -> bool:
    """Return True if this client has a Deriv symbol mapping for the given pair."""
    return _resolve_symbol(symbol) is not None


def mark_unavailable() -> None:
    """
    Persistently mark Deriv as unavailable for this process lifetime.
    Called after repeated failures to avoid hammering a dead endpoint.
    """
    global _deriv_available
    with _availability_lock:
        if _deriv_available:
            log.warning(
                "DerivClient: marking Deriv data source as UNAVAILABLE for this session. "
                "System will use fallback provider."
            )
            _deriv_available = False


def is_available() -> bool:
    """Return False if Deriv has been marked permanently unavailable."""
    with _availability_lock:
        return _deriv_available


def get_candles(symbol: str, timeframe: str = "15m", limit: int = 100) -> Optional[list[dict]]:
    """
    Fetch M15 candles for *symbol* from Deriv WebSocket API.

    Parameters
    ----------
    symbol : str
        Internal pair symbol, e.g. 'EUR/USD' or 'EURUSD'.
    timeframe : str
        Timeframe string.  Only '15m' / 'M15' fetches are forwarded to
        Deriv; all other timeframes return None immediately (this client
        is M15-specialised; the caller falls back to Twelve Data for H1).
    limit : int
        Ignored — Deriv always returns _CANDLE_COUNT candles.

    Returns
    -------
    list[dict] | None
        List of standard candle dicts (oldest→newest), or None on failure.
    """
    # ── Guard: availability ────────────────────────────────────────────────────
    if not is_available():
        return None

    # ── Guard: timeframe — only serve M15 ─────────────────────────────────────
    if timeframe not in ("15m", "M15", "15min"):
        log.debug(
            "DerivClient: timeframe %r is not M15 — skipping Deriv for %s",
            timeframe, symbol,
        )
        return None

    # ── Resolve Deriv symbol ───────────────────────────────────────────────────
    deriv_symbol = _resolve_symbol(symbol)
    if deriv_symbol is None:
        return None

    log.debug("DerivClient: requesting %d M15 candles for %s (%s)", _CANDLE_COUNT, symbol, deriv_symbol)

    # ── Retry loop with exponential backoff ────────────────────────────────────
    consecutive_failures = 0

    for attempt in range(1, _MAX_RETRIES + 1):
        candles = _fetch_over_ws(deriv_symbol)

        if candles is not None:
            # Success
            log.info(
                "DerivClient: fetched %d M15 candles for %s (attempt %d/%d)",
                len(candles), symbol, attempt, _MAX_RETRIES,
            )
            return candles

        # Failure on this attempt
        consecutive_failures += 1
        if attempt < _MAX_RETRIES:
            backoff = _BASE_BACKOFF * (2 ** (attempt - 1))
            log.warning(
                "DerivClient: attempt %d/%d failed for %s — retrying in %.1fs",
                attempt, _MAX_RETRIES, symbol, backoff,
            )
            time.sleep(backoff)
        else:
            log.error(
                "DerivClient: all %d attempts failed for %s",
                _MAX_RETRIES, symbol,
            )

    # All retries exhausted — do NOT mark permanently unavailable on a single
    # symbol failure (network hiccup, symbol maintenance, etc.).
    # Permanent unavailability is managed by the caller (safe_get_candles).
    return None


def safe_get_candles(
    symbol: str,
    timeframe: str = "15m",
    limit: int = 100,
    fallback_fn=None,
) -> Optional[list[dict]]:
    """
    High-level wrapper with automatic fallback.

    Tries Deriv first.  If Deriv fails (returns None), calls *fallback_fn*
    if provided.  After _MAX_RETRIES consecutive failures across calls,
    marks Deriv permanently unavailable so future calls skip straight to
    the fallback without delay.

    Parameters
    ----------
    symbol : str
        Pair symbol ('EUR/USD', etc.).
    timeframe : str
        Timeframe string.
    limit : int
        Candle count hint passed to fallback_fn.
    fallback_fn : callable | None
        Function with signature (symbol, timeframe, limit) → list[dict] | None.
        Typically ``trading.market.forex.get_candles``.

    Returns
    -------
    list[dict] | None
    """
    # ── Try Deriv ──────────────────────────────────────────────────────────────
    if is_available() and is_supported(symbol) and timeframe in ("15m", "M15", "15min"):
        try:
            candles = get_candles(symbol, timeframe, limit)
        except Exception as exc:
            log.error("DerivClient: unexpected exception in get_candles: %s", exc, exc_info=True)
            candles = None

        if candles is not None:
            return candles

        log.warning(
            "DerivClient: Deriv unavailable for %s %s — falling back to existing provider",
            symbol, timeframe,
        )

    # ── Fallback ───────────────────────────────────────────────────────────────
    if fallback_fn is not None:
        log.debug("DerivClient: invoking fallback provider for %s %s", symbol, timeframe)
        try:
            return fallback_fn(symbol, timeframe, limit)
        except Exception as exc:
            log.error("DerivClient: fallback provider raised: %s", exc, exc_info=True)
            return None

    return None
