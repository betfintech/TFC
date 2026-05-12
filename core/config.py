"""
core/config.py — Configuration (UPDATED - More Signals)
======================================================
Changes:
- Volatility thresholds REDUCED for more signal generation
- Session filter DISABLED for 24h trading coverage
- All other configs preserved
"""
from __future__ import annotations

import logging
import os
import secrets

_log = logging.getLogger("core.config")


def _safe_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    if not raw:
        _log.debug("Config: %s not set — using default %s", key, default)
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        _log.warning("Config: %s=%r is not a valid int — using default %s", key, raw, default)
        return default


def _safe_float(key: str, default: float) -> float:
    raw = os.getenv(key, "")
    if not raw:
        _log.debug("Config: %s not set — using default %s", key, default)
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        _log.warning("Config: %s=%r is not a valid float — using default %s", key, raw, default)
        return default


def _safe_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ── OpenRouter AI (for chat) ──────────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL: str   = os.getenv("OPENROUTER_MODEL", "openrouter/auto")  # auto-selects best model

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID: int           = _safe_int("ADMIN_ID", 0)
CHANNEL_ID: int         = _safe_int("CHANNEL_ID", 0)

# ── Binance (crypto only) ─────────────────────────────────────────────────────
BINANCE_BASE_URL: str = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")

# ── Deriv WebSocket API ───────────────────────────────────────────────────────
# Optional — Deriv allows public candle history without authentication.
DERIV_API_TOKEN: str = os.getenv("DERIV_API_TOKEN", "")

# DERIV_APP_ID must be a numeric app ID (e.g. 1089).
# If the env var holds a non-numeric OAuth key, fall back to the public demo ID.
_raw_app_id = os.getenv("DERIV_APP_ID", "")
try:
    DERIV_APP_ID: int = int(_raw_app_id)
except (ValueError, TypeError):
    DERIV_APP_ID = 1089  # Deriv public/demo app_id
    if _raw_app_id:
        _log.warning(
            "Config: DERIV_APP_ID=%r is not a valid integer (looks like an OAuth key) "
            "— using fallback app_id=1089. Set DERIV_APP_ID=1089 in your .env.",
            _raw_app_id,
        )

_log.info("Loaded DERIV_APP_ID=%s", DERIV_APP_ID)

# ── App ───────────────────────────────────────────────────────────────────────
APP_ENV: str   = os.getenv("APP_ENV", "production")
DEBUG: bool    = _safe_bool("DEBUG", False)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

if LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    _log.warning("Config: LOG_LEVEL=%r is invalid — defaulting to INFO", LOG_LEVEL)
    LOG_LEVEL = "INFO"

# ── Trading ───────────────────────────────────────────────────────────────────
_raw_interval      = _safe_int("MAIN_LOOP_INTERVAL", 60)
MAIN_LOOP_INTERVAL = max(60, min(120, _raw_interval))

MAX_TRADES_PER_HOUR: int = max(1, _safe_int("MAX_TRADES_PER_HOUR", 5))  # Softened: was 3 → 5
MAX_TRADES_PER_DAY: int  = max(1, _safe_int("MAX_TRADES_PER_DAY", 15))  # Softened: was 8 → 15

# Drought threshold: if the whole bot hasn't sent any signal for this many hours,
# the engine logs a warning so you know something may be wrong with data feeds.
SIGNAL_DROUGHT_ALERT_HOURS: int = max(1, _safe_int("SIGNAL_DROUGHT_ALERT_HOURS", 6))

# ── Volatility (RELAXED for more signals) ─────────────────────────────────────
# Changed from 0.003 to 0.002 (crypto) and 0.001 to 0.0005 (forex)
CRYPTO_VOLATILITY_THRESHOLD: float = _safe_float("CRYPTO_VOLATILITY_THRESHOLD", 0.002)
FOREX_VOLATILITY_THRESHOLD: float  = _safe_float("FOREX_VOLATILITY_THRESHOLD", 0.0005)

# ── Sessions (DISABLED for 24h coverage) ──────────────────────────────────────
# Changed from True to False — signals across all sessions
ENABLE_SESSION_FILTER: bool = _safe_bool("ENABLE_SESSION_FILTER", False)

# ── Payment ───────────────────────────────────────────────────────────────────
PAYMENT_BANK: str      = os.getenv("PAYMENT_BANK", "Moniepoint")
PAYMENT_ACCOUNT: str   = os.getenv("PAYMENT_ACCOUNT", "")
PAYMENT_NAME: str      = os.getenv("PAYMENT_NAME", "")
PAYMENT_AMOUNT: int    = max(0, _safe_int("PAYMENT_AMOUNT", 10000))
SUBSCRIPTION_DAYS: int = max(1, _safe_int("SUBSCRIPTION_DAYS", 30))

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY: str = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    _log.warning(
        "Config: SECRET_KEY not set — generated ephemeral key. "
        "Set SECRET_KEY in .env for stable sessions across restarts."
    )

# ── Watchlists ────────────────────────────────────────────────────────────────
# 4 crypto pairs (trade 24/7) + 6 forex pairs (London + NY sessions)
# = 10 pairs total → realistically 10-15 signals per day
# ── Trading Pairs ──────────────────────────────────────────────────────────────
# Crypto — Deriv supports these via WebSocket (cryXXXUSD synthetic feed)
# If a pair doesn't exist on Deriv, it will log "no Deriv symbol" and skip it.
# That's OK — the bot will just trade the ones that work.
CRYPTO_PAIRS: list[str] = [
    "BTCUSDT",   # Bitcoin — always available
    "ETHUSDT",   # Ethereum — always available
    "SOLUSDT",   # Solana — high volatility
    "BNBUSDT",   # BNB — Binance Coin
    "XRPUSDT",   # XRP — Ripple
    "ADAUSDT",   # Cardano
    "DOGEUSDT",  # Doge — popular meme coin
    "LTCUSDT",   # Litecoin — classic altcoin
]

# Forex — 15 pairs (majors + popular crosses)
FOREX_PAIRS: list[str] = [
    # ── MAJORS (7) ──
    "EUR/USD",   # Most traded globally
    "GBP/USD",   # High volatility
    "USD/JPY",   # Safe-haven, BoJ sensitivity
    "AUD/USD",   # Commodity-linked
    "USD/CAD",   # Oil-correlated
    "USD/CHF",   # Safe-haven
    "NZD/USD",   # Commodity-linked
    # ── CROSSES (8) ──
    "GBP/JPY",   # Very volatile, strong setups
    "EUR/JPY",   # Trend continuation pair
    "AUD/JPY",   # Risk-on/risk-off indicator
    "EUR/GBP",   # Range and breakout setups
    "GBP/CAD",   # High volatility cross
    "EUR/CAD",   # Trend-following pair
    "EUR/CHF",   # Smooth trends
    "CAD/JPY",   # Oil + rate differential trades
]

# ── Timeframes ────────────────────────────────────────────────────────────────
# Engine-native timeframe strings
TF_M1:  str = "M1"
TF_M5:  str = "M5"
TF_M15: str = "M15"
TF_H1:  str = "H1"


def validate() -> None:
    """
    Raise EnvironmentError if critical env vars are missing.
    Reports ALL missing vars at once.
    """
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if not CHANNEL_ID:
        missing.append("CHANNEL_ID")

    warnings = []
    if not PAYMENT_ACCOUNT:
        warnings.append("PAYMENT_ACCOUNT (payment instructions incomplete)")
    if not PAYMENT_NAME:
        warnings.append("PAYMENT_NAME (payment instructions incomplete)")

    for w in warnings:
        _log.warning("Config warning: %s not set", w)

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Set these in your .env file before starting.\n"
            f"Example: TELEGRAM_BOT_TOKEN=123:ABC ADMIN_ID=111 CHANNEL_ID=-100111"
)

