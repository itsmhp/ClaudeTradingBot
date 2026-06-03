"""
core/market_regime.py
=====================
Detects the current market regime for a symbol using EMA, ATR, and RSI.

Regimes:
  TRENDING_BULL  — EMA50 > EMA200, price trending up, normal/low volatility
  TRENDING_BEAR  — EMA50 < EMA200, price trending down, normal/low volatility
  RANGING        — EMA50 ≈ EMA200, ADX < 20, price oscillating
  VOLATILE       — ATR > 2× 50-period average ATR (avoid trading)

Strategy recommendation:
  TRENDING → SWING
  RANGING  → SCALPING
  VOLATILE → AVOID
"""
from __future__ import annotations

import asyncio
import math
from typing import Optional

from loguru import logger


def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA using standard multiplier formula."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema_values = [sum(values[:period]) / period]
    for v in values[period:]:
        ema_values.append(v * k + ema_values[-1] * (1 - k))
    return ema_values


def _rsi(closes: list[float], period: int = 14) -> float:
    """Compute RSI for the last bar."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Compute ATR values."""
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return trs
    atrs = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        atrs.append((atrs[-1] * (period - 1) + tr) / period)
    return atrs


class MarketRegimeDetector:
    """Detects market regime (TRENDING_BULL/BEAR, RANGING, VOLATILE) from live candle data."""

    WATCHLIST = ["XAUUSD", "BTCUSD", "USDJPY", "EURUSD", "GBPUSD", "NAS100", "US30"]

    def __init__(self, mt5_bridge=None) -> None:
        self._mt5_bridge = mt5_bridge
        self._regime_cache: dict[str, dict] = {}

    def _get_mt5_bridge(self):
        if self._mt5_bridge is None:
            from core.mt5_bridge import MT5Bridge
            self._mt5_bridge = MT5Bridge()
        return self._mt5_bridge

    async def detect_regime(self, symbol: str, timeframe: str = "D1") -> dict:
        """Load last 250 candles and compute market regime.

        Returns a regime dict with keys:
            symbol, regime, trend, volatility, momentum, rsi, atr_ratio,
            recommended_strategy, reasoning
        """
        bridge = self._get_mt5_bridge()

        # Map timeframe string to MT5 constant
        tf_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 16385, "H4": 16388, "D1": 16408,
        }

        try:
            # Fetch candles via bridge
            candles = await bridge.get_candles(symbol, tf_map.get(timeframe, 16408), count=250)
            if not candles or len(candles) < 60:
                return self._fallback_regime(symbol, "Insufficient candle data")

            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]

        except Exception as exc:
            logger.warning(f"[Regime] Failed to get candles for {symbol}: {exc}")
            return self._fallback_regime(symbol, f"MT5 error: {exc}")

        # EMA calculations
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200)

        if not ema50 or not ema200:
            return self._fallback_regime(symbol, "Not enough data for EMA200")

        last_ema50 = ema50[-1]
        last_ema200 = ema200[-1] if len(ema200) >= 1 else last_ema50

        # EMA slope (over last 5 bars of EMA50)
        ema50_slope = (ema50[-1] - ema50[-5]) / ema50[-5] * 100 if len(ema50) >= 5 else 0.0

        # ATR volatility
        atr_vals = _atr(highs, lows, closes, period=14)
        current_atr = atr_vals[-1] if atr_vals else 0.0
        avg_atr_50 = sum(atr_vals[-50:]) / min(50, len(atr_vals)) if atr_vals else 0.0
        atr_ratio = round(current_atr / avg_atr_50, 2) if avg_atr_50 > 0 else 1.0

        # RSI
        rsi_val = _rsi(closes, 14)
        momentum = "rising" if rsi_val > 55 else "falling" if rsi_val < 45 else "flat"

        # Trend direction
        if last_ema50 > last_ema200 * 1.002:
            trend = "bullish"
        elif last_ema50 < last_ema200 * 0.998:
            trend = "bearish"
        else:
            trend = "neutral"

        # Regime classification
        is_trending = abs(ema50_slope) > 0.1
        is_volatile = atr_ratio > 2.0

        if is_volatile:
            regime = "VOLATILE"
            volatility = "high"
            recommended = "AVOID"
            reasoning = (
                f"ATR is {atr_ratio:.1f}× above average. Market too volatile for safe entries."
            )
        elif is_trending and trend == "bullish":
            regime = "TRENDING_BULL"
            volatility = "high" if atr_ratio > 1.5 else "normal"
            recommended = "SWING"
            reasoning = (
                f"EMA50 ({last_ema50:.4f}) > EMA200 ({last_ema200:.4f}). "
                f"Uptrend slope {ema50_slope:.2f}%. RSI={rsi_val}. Best for SWING longs."
            )
        elif is_trending and trend == "bearish":
            regime = "TRENDING_BEAR"
            volatility = "high" if atr_ratio > 1.5 else "normal"
            recommended = "SWING"
            reasoning = (
                f"EMA50 ({last_ema50:.4f}) < EMA200 ({last_ema200:.4f}). "
                f"Downtrend slope {ema50_slope:.2f}%. RSI={rsi_val}. Best for SWING shorts."
            )
        else:
            regime = "RANGING"
            volatility = "low" if atr_ratio < 0.8 else "normal"
            recommended = "SCALPING"
            reasoning = (
                f"EMAs converging (slope {ema50_slope:.2f}%). "
                f"ATR ratio {atr_ratio:.2f}. RSI={rsi_val}. Best for SCALPING range plays."
            )

        result = {
            "symbol": symbol,
            "regime": regime,
            "trend": trend,
            "volatility": volatility,
            "momentum": momentum,
            "rsi": rsi_val,
            "atr_ratio": atr_ratio,
            "ema50": round(last_ema50, 5),
            "ema200": round(last_ema200, 5),
            "recommended_strategy": recommended,
            "reasoning": reasoning,
        }

        self._regime_cache[symbol] = result
        return result

    def _fallback_regime(self, symbol: str, reason: str) -> dict:
        return {
            "symbol": symbol,
            "regime": "RANGING",
            "trend": "neutral",
            "volatility": "normal",
            "momentum": "flat",
            "rsi": 50.0,
            "atr_ratio": 1.0,
            "ema50": 0.0,
            "ema200": 0.0,
            "recommended_strategy": "SWING",
            "reasoning": f"Fallback (could not compute): {reason}",
        }

    def select_strategy_for_pair(self, symbol: str) -> str:
        """Return strategy string from cached regime.

        Returns "SWING", "SCALPING", or "AVOID".
        """
        cached = self._regime_cache.get(symbol)
        if cached:
            return cached.get("recommended_strategy", "SWING")
        return "SWING"

    async def get_regime_for_all_pairs(self) -> dict[str, dict]:
        """Run detect_regime concurrently for all watchlist pairs."""
        tasks = [self.detect_regime(sym) for sym in self.WATCHLIST]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output: dict[str, dict] = {}
        for sym, res in zip(self.WATCHLIST, results):
            if isinstance(res, Exception):
                logger.warning(f"[Regime] Error for {sym}: {res}")
                output[sym] = self._fallback_regime(sym, str(res))
            else:
                output[sym] = res  # type: ignore[assignment]
        return output

    def get_cached_regimes(self) -> dict[str, dict]:
        """Return last computed regime cache (for dashboard/API)."""
        return dict(self._regime_cache)
