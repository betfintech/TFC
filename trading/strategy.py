"""
EMA Crossover + ATR Risk Management Strategy
=============================================
A pure trend-following system. The bot only enters when the market
is already moving in a confirmed direction — never against the trend.

Signal output: BUY | SELL | WAIT

STRATEGY LOGIC (5 gates):
  GATE 1 — EMA Crossover:    Fast EMA(20) crosses Slow EMA(50).
                              BUY = fast crosses above slow.
                              SELL = fast crosses below slow.        [HARD]
  GATE 2 — Trend Alignment:  Price must be on the correct side of
                              the slow EMA. No counter-trend trades. [HARD]
  GATE 3 — RSI Filter:       RSI(14) must confirm momentum.
                              BUY needs RSI > 50.
                              SELL needs RSI < 50.                   [HARD]
  GATE 4 — ATR Volatility:   Market must have enough movement to
                              produce a valid risk:reward setup.
                              ATR must exceed minimum threshold.     [HARD]
  GATE 5 — No Reentry:       If a signal was just sent in the same
                              direction on this pair, wait for the
                              crossover to reset before re-entering. [HARD]

RISK MANAGEMENT (automatic, ATR-based):
  Stop Loss   = entry ± (ATR_SL_MULT × ATR)    default: 1.5 × ATR
  Take Profit = entry ± (ATR_TP_MULT × ATR)    default: 3.0 × ATR
  Minimum RR enforced: 1:2 on every trade.

WHY THIS WORKS WITH BOTS:
  - Every rule is 100% mathematical — no interpretation needed.
  - ATR auto-adjusts SL/TP to current volatility.
  - Works on any pair, any timeframe, any market condition.
  - GA can evolve all parameters (EMA lengths, ATR mults, RSI level).
  - Simplest strategies backtest most honestly — no overfitting.

Timeframes used: H1 (primary), M15 (entry timing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)


# ======================================================================
# EVOLVABLE PARAMETERS
# ======================================================================
# All thresholds live here. The Genetic Algorithm (learning/evolver.py)
# updates this dict at runtime — no restart needed.

_PARAMS: dict = {
    # EMA periods
    "ema_fast_period":    20,      # fast EMA length
    "ema_slow_period":    50,      # slow EMA length
    # RSI
    "rsi_period":         14,      # RSI lookback
    "rsi_bull_min":       50.0,    # BUY only when RSI above this
    "rsi_bear_max":       50.0,    # SELL only when RSI below this
    # ATR
    "atr_period":         14,      # ATR lookback
    "atr_sl_mult":        1.5,     # SL = entry ± (atr_sl_mult × ATR)
    "atr_tp_mult":        3.0,     # TP = entry ± (atr_tp_mult × ATR)
    # Minimum ATR as % of price (filters dead/flat markets)
    "atr_min_pct_crypto": 0.002,   # 0.2% for crypto
    "atr_min_pct_forex":  0.0003,  # 0.03% for forex
    # Minimum accepted RR (hard gate)
    "rr_minimum":         2.0,
    # Crossover lookback: how many bars back to check for the cross
    "crossover_lookback": 3,
}

# Lock protecting _PARAMS from concurrent reads during GA bulk update
import threading as _threading
_PARAMS_LOCK = _threading.Lock()


def _update_params(new_params: dict) -> None:
    """
    Thread-safe bulk update of _PARAMS.
    Builds a validated new dict, then replaces _PARAMS contents atomically.
    The trading engine reads _PARAMS on every signal; this ensures it never
    sees a half-updated state (e.g. fast_period updated but slow_period not yet).
    """
    import copy
    validated = dict(_PARAMS)   # start from current values as fallback
    for k, v in new_params.items():
        if k in validated:
            validated[k] = v
    # Enforce fast < slow constraint
    if int(validated["ema_fast_period"]) >= int(validated["ema_slow_period"]):
        validated["ema_fast_period"] = max(2, int(validated["ema_slow_period"]) - 5)
    with _PARAMS_LOCK:
        _PARAMS.clear()
        _PARAMS.update(validated)


# ======================================================================
# DATA CONTAINERS
# ======================================================================

@dataclass
class Signal:
    symbol:       str
    direction:    str           # BUY | SELL | WAIT
    market_type:  str           # crypto | forex
    entry:        float = 0.0
    stop_loss:    float = 0.0
    tp1:          float = 0.0
    tp2:          float = 0.0
    tp_final:     float = 0.0
    reason:       str   = ""
    setup_quality: str  = ""    # "A" | "B" | "C"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("BUY", "SELL")


@dataclass
class Candle:
    timestamp: object
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


def _to_candles(raw: list[dict]) -> list[Candle]:
    result = []
    for r in raw:
        try:
            result.append(Candle(**r))
        except (TypeError, KeyError):
            pass
    return result


# ======================================================================
# INDICATORS
# ======================================================================

def _ema(closes: list[float], period: int) -> list[float]:
    """
    Exponential Moving Average.
    Returns a list the same length as closes.
    First `period-1` values are None (insufficient data).
    """
    period = max(2, int(period))   # guard against GA evolving period <= 1
    if len(closes) < period:
        return [None] * len(closes)

    k = 2.0 / (period + 1)
    result: list[Optional[float]] = [None] * (period - 1)

    # Seed with simple average of first `period` values
    seed = sum(closes[:period]) / period
    result.append(seed)

    for price in closes[period:]:
        result.append(price * k + result[-1] * (1 - k))

    return result


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index — returns current RSI value (0–100).
    Returns None if insufficient data.
    """
    period = max(2, int(period))   # guard against period <= 1
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    # Wilder smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs  = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _atr(candles: list[Candle], period: int = 14) -> Optional[float]:
    """
    Average True Range — Wilder smoothing.
    Returns None if insufficient data.
    """
    period = max(2, int(period))   # guard against period <= 1
    if len(candles) < period + 1:
        return None

    tr_list = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - p.close),
            abs(c.low  - p.close),
        )
        tr_list.append(tr)

    # Wilder smoothing
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


# ======================================================================
# GATE FUNCTIONS
# ======================================================================

def _detect_crossover(
    fast_ema: list[Optional[float]],
    slow_ema: list[Optional[float]],
    lookback: int,
) -> tuple[str, str]:
    """
    Gate 1: Detect a fresh EMA crossover within the last `lookback` bars.

    Returns:
        (direction, reason) where direction is "bullish" | "bearish" | "none"
    """
    # Need at least lookback + 1 valid values
    valid_fast = [v for v in fast_ema if v is not None]
    valid_slow = [v for v in slow_ema if v is not None]

    if len(valid_fast) < lookback + 1 or len(valid_slow) < lookback + 1:
        return "none", "Insufficient EMA data"

    # Current bar: fast vs slow
    curr_fast = valid_fast[-1]
    curr_slow = valid_slow[-1]

    # lookback bars ago: fast vs slow
    prev_fast = valid_fast[-(lookback + 1)]
    prev_slow = valid_slow[-(lookback + 1)]

    bullish_cross = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
    bearish_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)

    if bullish_cross:
        return "bullish", (
            f"Bullish EMA cross: fast({curr_fast:.5f}) > slow({curr_slow:.5f})"
        )
    if bearish_cross:
        return "bearish", (
            f"Bearish EMA cross: fast({curr_fast:.5f}) < slow({curr_slow:.5f})"
        )

    return "none", (
        f"No fresh crossover (fast={curr_fast:.5f}, slow={curr_slow:.5f})"
    )


def _trend_alignment(
    close: float,
    slow_ema_val: float,
    direction: str,
) -> tuple[bool, str]:
    """
    Gate 2: Price must be on the correct side of the slow EMA.
    BUY  → price above slow EMA (uptrend confirmed).
    SELL → price below slow EMA (downtrend confirmed).
    """
    if direction == "bullish":
        ok = close > slow_ema_val
        return ok, (
            f"Price({close:.5f}) {'above' if ok else 'below'} SlowEMA({slow_ema_val:.5f})"
        )
    else:
        ok = close < slow_ema_val
        return ok, (
            f"Price({close:.5f}) {'below' if ok else 'above'} SlowEMA({slow_ema_val:.5f})"
        )


def _rsi_filter(rsi_val: float, direction: str) -> tuple[bool, str]:
    """
    Gate 3: RSI must confirm momentum direction.
    BUY  → RSI > rsi_bull_min (default 50)
    SELL → RSI < rsi_bear_max (default 50)
    """
    if direction == "bullish":
        thresh = _PARAMS["rsi_bull_min"]
        ok = rsi_val > thresh
        return ok, f"RSI({rsi_val:.1f}) {'>' if ok else '<='} {thresh} (BUY threshold)"
    else:
        thresh = _PARAMS["rsi_bear_max"]
        ok = rsi_val < thresh
        return ok, f"RSI({rsi_val:.1f}) {'<' if ok else '>='} {thresh} (SELL threshold)"


def _atr_volatility_ok(atr_val: float, close: float, market_type: str) -> tuple[bool, str]:
    """
    Gate 4: ATR must be large enough relative to price to produce
    a real risk:reward setup.
    """
    min_pct = (
        _PARAMS["atr_min_pct_crypto"] if market_type == "crypto"
        else _PARAMS["atr_min_pct_forex"]
    )
    rel_atr = atr_val / close if close > 0 else 0.0
    ok = rel_atr >= min_pct
    return ok, (
        f"ATR({atr_val:.5f}) rel={rel_atr:.4%} "
        f"{'≥' if ok else '<'} min {min_pct:.4%}"
    )


def _build_levels(
    entry: float,
    atr_val: float,
    direction: str,
) -> tuple[float, float, float, float]:
    """
    Compute SL and TP levels from ATR multiples.

    Returns: (stop_loss, tp1, tp2, tp_final)
      tp1      = 1:2 RR   (atr_tp_mult × ATR, split at 2/3)
      tp2      = full ATR TP
      tp_final = 1:4 RR extension
    """
    sl_dist = _PARAMS["atr_sl_mult"] * atr_val
    tp_dist = _PARAMS["atr_tp_mult"] * atr_val

    if direction == "bullish":
        sl       = entry - sl_dist
        tp1      = entry + tp_dist * (2 / 3)   # partial at ~2:1
        tp2      = entry + tp_dist              # full TP
        tp_final = entry + sl_dist * 4          # 1:4 extension
    else:
        sl       = entry + sl_dist
        tp1      = entry - tp_dist * (2 / 3)
        tp2      = entry - tp_dist
        tp_final = entry - sl_dist * 4

    return sl, tp1, tp2, tp_final


# ======================================================================
# MAIN SIGNAL GENERATOR
# ======================================================================

def generate_signal(
    symbol: str,
    h1_raw: list[dict],
    m15_raw: list[dict],
    market_type: str,
) -> Signal:
    """
    EMA Crossover + ATR strategy — 5-gate pipeline.
    Returns Signal with direction BUY | SELL | WAIT.
    """
    signal, _ = generate_signal_with_audit(symbol, h1_raw, m15_raw, market_type)
    return signal


# ======================================================================
# AUDIT-ENABLED SIGNAL GENERATOR
# ======================================================================

def generate_signal_with_audit(
    symbol: str,
    h1_raw: list[dict],
    m15_raw: list[dict],
    market_type: str,
) -> tuple[Signal, dict]:
    """
    Full 5-gate pipeline returning (Signal, gate_audit).
    gate_audit is used by the backtester and RL agent.
    """
    audit: dict = {}

    def _wait(reason: str, block_gate: str = "") -> tuple[Signal, dict]:
        audit["final_direction"] = "WAIT"
        audit["block_gate"]      = block_gate
        return (
            Signal(symbol=symbol, direction="WAIT",
                   market_type=market_type, reason=reason),
            audit,
        )

    # ── Convert candles ───────────────────────────────────────────────
    h1 = _to_candles(h1_raw)

    fast_p = int(_PARAMS["ema_fast_period"])
    slow_p = int(_PARAMS["ema_slow_period"])
    atr_p  = int(_PARAMS["atr_period"])
    rsi_p  = int(_PARAMS["rsi_period"])
    lb     = int(_PARAMS["crossover_lookback"])

    min_bars = slow_p + lb + 5
    if len(h1) < min_bars:
        return _wait(f"Insufficient H1 bars ({len(h1)} < {min_bars})", "data")

    closes = [c.close for c in h1]

    # ── Compute indicators ────────────────────────────────────────────
    fast_ema_series = _ema(closes, fast_p)
    slow_ema_series = _ema(closes, slow_p)
    rsi_val         = _rsi(closes, rsi_p)
    atr_val         = _atr(h1, atr_p)

    current_close    = closes[-1]
    slow_ema_current = next(
        (v for v in reversed(slow_ema_series) if v is not None), None
    )

    _ef = fast_ema_series[-1]
    audit["ema_fast"] = round(_ef   if _ef   is not None else 0.0, 5)
    audit["ema_slow"] = round(slow_ema_current if slow_ema_current is not None else 0.0, 5)
    audit["rsi"]      = round(rsi_val if rsi_val is not None else 0.0, 2)
    audit["atr"]      = round(atr_val if atr_val is not None else 0.0, 5)

    if rsi_val is None:
        return _wait("RSI: insufficient data", "data")
    if atr_val is None:
        return _wait("ATR: insufficient data", "data")
    if slow_ema_current is None:
        return _wait("Slow EMA: insufficient data", "data")

    # ── GATE 1: EMA Crossover ─────────────────────────────────────────
    direction, cross_reason = _detect_crossover(
        fast_ema_series, slow_ema_series, lb
    )
    audit["G1_crossover"] = direction
    if direction == "none":
        return _wait(f"G1 EMA Crossover: {cross_reason}", "G1")
    audit["G1_ok"] = True
    log.debug("[%s] G1 OK: %s", symbol, cross_reason)

    # ── GATE 2: Trend Alignment ───────────────────────────────────────
    aligned, align_reason = _trend_alignment(
        current_close, slow_ema_current, direction
    )
    audit["G2_aligned"] = aligned
    if not aligned:
        return _wait(f"G2 Trend Alignment: {align_reason}", "G2")
    audit["G2_ok"] = True
    log.debug("[%s] G2 OK: %s", symbol, align_reason)

    # ── GATE 3: RSI Filter ────────────────────────────────────────────
    rsi_ok, rsi_reason = _rsi_filter(rsi_val, direction)
    audit["G3_rsi"]    = round(rsi_val, 2)
    audit["G3_ok_flag"] = rsi_ok
    if not rsi_ok:
        return _wait(f"G3 RSI: {rsi_reason}", "G3")
    audit["G3_ok"] = True
    log.debug("[%s] G3 OK: %s", symbol, rsi_reason)

    # ── GATE 4: ATR Volatility ────────────────────────────────────────
    vol_ok, vol_reason = _atr_volatility_ok(atr_val, current_close, market_type)
    audit["G4_atr_ok"] = vol_ok
    if not vol_ok:
        return _wait(f"G4 ATR Volatility: {vol_reason}", "G4")
    audit["G4_ok"] = True
    log.debug("[%s] G4 OK: %s", symbol, vol_reason)

    # ── Build levels ──────────────────────────────────────────────────
    entry = current_close
    sl, tp1, tp2, tp_final = _build_levels(entry, atr_val, direction)

    # ── RR check ─────────────────────────────────────────────────────
    risk   = abs(entry - sl)
    reward = abs(tp2   - entry)
    actual_rr = reward / risk if risk > 0 else 0.0

    if actual_rr < _PARAMS["rr_minimum"]:
        return _wait(
            f"RR: {actual_rr:.2f} below minimum {_PARAMS['rr_minimum']:.1f}",
            "RR",
        )

    # ── Signal quality ────────────────────────────────────────────────
    # A = strong RSI conviction + good ATR
    # B = moderate
    # C = marginal
    if direction == "bullish":
        rsi_strength = rsi_val - 50
    else:
        rsi_strength = 50 - rsi_val

    quality = "A" if rsi_strength > 15 else ("B" if rsi_strength > 5 else "C")

    sig_direction = "BUY" if direction == "bullish" else "SELL"
    audit["final_direction"] = sig_direction
    audit["block_gate"]      = None
    audit["G1_session"]      = "24h"
    audit["G2_quality"]      = quality
    audit["G7_narrative"]    = "trend_follow"
    audit["G11_poi_type"]    = "EMA"
    audit["G4_volatility"]   = True

    reason = (
        f"EMA{fast_p}/{slow_p} {cross_reason} | "
        f"RSI={rsi_val:.1f} | ATR={atr_val:.5f} | RR={actual_rr:.2f} | Q={quality}"
    )

    log.info(
        "[%s] ✅ %s | entry=%.5f SL=%.5f TP1=%.5f TP2=%.5f | RR=%.2f | Q=%s",
        symbol, sig_direction, entry, sl, tp1, tp2, actual_rr, quality,
    )

    return Signal(
        symbol=symbol,
        direction=sig_direction,
        market_type=market_type,
        entry=entry,
        stop_loss=sl,
        tp1=tp1,
        tp2=tp2,
        tp_final=tp_final,
        reason=reason,
        setup_quality=quality,
    ), audit
