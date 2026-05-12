"""
learning/regime_detector.py — Market Regime Classifier
=======================================================
Classifies the current market regime from H1 candles using ADX and ATR.

Regimes:
  HIGH_VOL_BULL   — Strong uptrend with elevated volatility
  HIGH_VOL_BEAR   — Strong downtrend with elevated volatility
  TRENDING_BULL   — Uptrend with normal volatility
  TRENDING_BEAR   — Downtrend with normal volatility
  LOW_VOL_RANGE   — Sideways / consolidation, low volatility
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from learning.memory import MemoryStore

log = get_logger(__name__)

# ADX thresholds
_ADX_TRENDING   = 25.0   # above → trending
_ADX_RANGING    = 20.0   # below → ranging

# Win-rate floor: skip trading in regimes below this
_MIN_REGIME_WIN_RATE = 0.40


class RegimeDetector:
    """
    Classifies market regime from H1 candle data.

    Uses ADX for trend strength and ATR ratio for volatility level.
    """

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    # ──────────────────────────────────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────────────────────────────────

    def detect(self, h1_candles: list[dict]) -> str:
        """
        Detect the current market regime from H1 candles.

        Returns one of: HIGH_VOL_BULL | HIGH_VOL_BEAR | TRENDING_BULL |
                        TRENDING_BEAR | LOW_VOL_RANGE
        """
        try:
            if len(h1_candles) < 30:
                return "LOW_VOL_RANGE"

            highs  = [c["high"]  for c in h1_candles]
            lows   = [c["low"]   for c in h1_candles]
            closes = [c["close"] for c in h1_candles]

            adx_val, plus_di, minus_di = self._adx(highs, lows, closes, period=14)
            atr_val = self._atr(highs, lows, closes, period=14)

            # ATR relative to 50-bar average close for volatility
            avg_close = sum(closes[-50:]) / min(50, len(closes))
            rel_atr = atr_val / avg_close if avg_close > 0 else 0.0
            # Compare against own 50-bar ATR average
            atr_50 = self._atr(highs, lows, closes, period=50)
            high_vol = rel_atr > (atr_50 / avg_close * 1.5) if avg_close > 0 else False

            bullish = plus_di > minus_di

            if adx_val >= _ADX_TRENDING:
                if high_vol:
                    return "HIGH_VOL_BULL" if bullish else "HIGH_VOL_BEAR"
                return "TRENDING_BULL" if bullish else "TRENDING_BEAR"
            return "LOW_VOL_RANGE"

        except Exception as exc:
            log.debug("[RegimeDetector] detect error: %s", exc)
            return "LOW_VOL_RANGE"

    def get_regime_win_rates(self) -> dict:
        """Return win rates per regime from memory."""
        try:
            with self._memory._lock:
                return dict(self._memory._data.get("market_regimes", {}))
        except Exception:
            return {}

    def should_trade_in_regime(self, regime: str) -> bool:
        """
        Return False if historical win rate for this regime is below threshold.
        Returns True when we have insufficient data (< 20 signals).
        """
        try:
            regimes = self.get_regime_win_rates()
            if regime not in regimes:
                return True  # no data → allow by default
            stats = regimes[regime]
            if stats.get("signal_count", 0) < 20:
                return True  # not enough data to decide
            return stats.get("win_rate", 1.0) >= _MIN_REGIME_WIN_RATE
        except Exception:
            return True

    # ──────────────────────────────────────────────────────────────────────────
    # Indicators
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _adx(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> tuple[float, float, float]:
        """
        Wilder ADX calculation.
        Returns (adx, +DI, -DI) as percentages.
        """
        if len(closes) < period + 1:
            return 0.0, 0.0, 0.0

        try:
            plus_dm  = []
            minus_dm = []
            tr_list  = []

            for i in range(1, len(closes)):
                up   = highs[i]  - highs[i - 1]
                down = lows[i - 1] - lows[i]

                plus_dm.append(up   if up > down and up > 0   else 0.0)
                minus_dm.append(down if down > up and down > 0 else 0.0)

                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i]  - closes[i - 1]),
                )
                tr_list.append(tr)

            def _wilder_smooth(values: list[float], p: int) -> list[float]:
                result = [sum(values[:p])]
                for v in values[p:]:
                    result.append(result[-1] - result[-1] / p + v)
                return result

            sm_tr   = _wilder_smooth(tr_list,   period)
            sm_pdm  = _wilder_smooth(plus_dm,   period)
            sm_mdm  = _wilder_smooth(minus_dm,  period)

            dx_list = []
            for i in range(len(sm_tr)):
                t = sm_tr[i]
                if t == 0:
                    dx_list.append(0.0)
                    continue
                pdi = 100.0 * sm_pdm[i] / t
                mdi = 100.0 * sm_mdm[i] / t
                denom = pdi + mdi
                dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

            # Final ADX = Wilder smooth of DX
            adx_series = _wilder_smooth(dx_list, period)
            adx = adx_series[-1] if adx_series else 0.0

            # Current +DI and -DI from last smooth values
            last_tr  = sm_tr[-1]  if sm_tr  else 1.0
            last_pdm = sm_pdm[-1] if sm_pdm else 0.0
            last_mdm = sm_mdm[-1] if sm_mdm else 0.0
            plus_di  = 100.0 * last_pdm / last_tr if last_tr > 0 else 0.0
            minus_di = 100.0 * last_mdm / last_tr if last_tr > 0 else 0.0

            return adx, plus_di, minus_di
        except Exception as exc:
            log.debug("[RegimeDetector] ADX calc error: %s", exc)
            return 0.0, 0.0, 0.0

    @staticmethod
    def _atr(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> float:
        """Simple ATR over the last `period` bars."""
        if len(closes) < 2:
            return 0.0
        try:
            tr_list = []
            for i in range(1, len(closes)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i]  - closes[i - 1]),
                )
                tr_list.append(tr)
            recent = tr_list[-period:]
            return sum(recent) / len(recent) if recent else 0.0
        except Exception:
            return 0.0
