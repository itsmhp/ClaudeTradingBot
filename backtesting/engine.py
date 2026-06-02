"""
backtesting/engine.py
=====================
Backtesting engine that runs swing and scalping strategies over historical
OHLCV data.  Uses vectorbt when available, falls back to a pure-pandas
simulation so the code runs even without vectorbt installed.

Usage::

    from backtesting.data_loader import HistoricalDataLoader
    from backtesting.engine import BacktestEngine

    loader = HistoricalDataLoader(bridge)
    engine = BacktestEngine(loader)
    stats = engine.run_swing_backtest("XAUUSD", "H4")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from .data_loader import DataLoadError, HistoricalDataLoader

# ── Optional vectorbt ────────────────────────────────────────────────────────
try:
    import vectorbt as vbt  # type: ignore[import]
    VBT_AVAILABLE = True
    logger.info("[BacktestEngine] vectorbt detected — using fast simulation.")
except ImportError:
    vbt = None  # type: ignore[assignment]
    VBT_AVAILABLE = False
    logger.warning("[BacktestEngine] vectorbt not found — using pandas fallback.")

RULES_PATH = Path(__file__).parent.parent / "strategies" / "rules.json"


# ── Indicator helpers ────────────────────────────────────────────────────────

def _ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _pandas_simulate(
    close: pd.Series,
    entries: pd.Series,
    exits: pd.Series,
    init_cash: float = 10_000.0,
    fees: float = 0.0001,
) -> dict[str, Any]:
    """Simple sequential pandas backtest (1 position at a time)."""
    equity = init_cash
    in_position = False
    entry_price = 0.0
    trades: list[float] = []

    for i in range(len(close)):
        if not in_position and entries.iloc[i]:
            entry_price = close.iloc[i]
            in_position = True
        elif in_position and exits.iloc[i]:
            pnl_pct = (close.iloc[i] - entry_price) / entry_price - fees * 2
            trades.append(pnl_pct)
            equity *= 1 + pnl_pct
            in_position = False

    if not trades:
        return _empty_stats()

    returns = pd.Series(trades)
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    total_ret = (equity - init_cash) / init_cash * 100

    # Max drawdown via equity curve
    eq_curve = pd.Series([init_cash * (1 + r) for r in returns.cumsum()])
    peak = eq_curve.cummax()
    drawdown = (eq_curve - peak) / peak
    max_dd = abs(drawdown.min()) * 100

    profit_factor = (
        wins.sum() / abs(losses.sum()) if len(losses) > 0 and abs(losses.sum()) > 0 else 0
    )
    sharpe = (
        returns.mean() / returns.std() * (252 ** 0.5) if returns.std() > 0 else 0
    )

    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0,
        "net_pnl": round(equity - init_cash, 2),
        "profit_factor": round(float(profit_factor), 3),
        "max_drawdown_pct": round(float(max_dd), 2),
        "sharpe_ratio": round(float(sharpe), 3),
        "avg_trade_duration_hours": 0,  # not tracked in simple sim
        "best_trade_pct": round(float(wins.max() * 100) if len(wins) else 0, 2),
        "worst_trade_pct": round(float(losses.min() * 100) if len(losses) else 0, 2),
        "total_return_pct": round(float(total_ret), 2),
        "trade_returns": [round(r * 100, 4) for r in trades],
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "net_pnl": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "avg_trade_duration_hours": 0.0,
        "best_trade_pct": 0.0,
        "worst_trade_pct": 0.0,
        "total_return_pct": 0.0,
        "trade_returns": [],
    }


class BacktestEngine:
    """Run swing and scalping backtests over historical OHLCV data.

    Parameters
    ----------
    data_loader:
        :class:`HistoricalDataLoader` instance.
    """

    def __init__(self, data_loader: HistoricalDataLoader) -> None:
        self._loader = data_loader
        self._watchlist: list[str] = []
        if RULES_PATH.exists():
            with open(RULES_PATH) as fh:
                self._watchlist = json.load(fh).get("watchlist", [])

    # ── Public API ───────────────────────────────────────────────────────────

    def run_swing_backtest(
        self,
        symbol: str,
        timeframe: str = "H4",
        count: int = 5000,
        init_cash: float = 10_000.0,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Run a swing strategy backtest.

        Strategy logic:
        - EMA 50, EMA 200, RSI 14
        - BUY: close > ema50, ema50 > ema200, RSI 45-70, recent ema50 cross-up
        - SELL: close < ema50, ema50 < ema200, RSI 30-55, recent ema50 cross-down

        Returns a stats dict.
        """
        p = params or {}
        ema_fast = p.get("ema_fast", 50)
        ema_slow = p.get("ema_slow", 200)
        rsi_low  = p.get("rsi_low",  45)
        rsi_high = p.get("rsi_high", 70)

        df = self._load_data(symbol, timeframe, count)
        close = df["close"]

        ema_f = _ema(close, ema_fast)
        ema_s = _ema(close, ema_slow)
        rsi   = _rsi(close, 14)

        cross_up   = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
        cross_down = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))

        buy_entries  = (close > ema_f) & (ema_f > ema_s) & rsi.between(rsi_low, rsi_high) & cross_up.rolling(3).max().astype(bool)
        sell_entries = (close < ema_f) & (ema_f < ema_s) & rsi.between(30, rsi_low + 10) & cross_down.rolling(3).max().astype(bool)

        buy_exits  = (close < ema_f) | (rsi > 75)
        sell_exits = (close > ema_f) | (rsi < 25)

        stats = self._run_sim(close, buy_entries, sell_entries, buy_exits, sell_exits, init_cash)
        period_days = int((df.index[-1] - df.index[0]).days) if len(df) > 1 else 0

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": "swing",
            "period_days": period_days,
            **stats,
        }

    def run_scalping_backtest(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 10_000,
        init_cash: float = 10_000.0,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Run a scalping strategy backtest.

        Strategy logic:
        - EMA 9, EMA 21, RSI 14
        - BUY: ema9 crosses above ema21, RSI 40-65
        - SELL: ema9 crosses below ema21, RSI 35-60
        """
        p = params or {}
        ema_fast = p.get("ema_fast", 9)
        ema_slow = p.get("ema_slow", 21)
        rsi_low  = p.get("rsi_low",  40)
        rsi_high = p.get("rsi_high", 65)

        df = self._load_data(symbol, timeframe, count)
        close = df["close"]

        ema_f = _ema(close, ema_fast)
        ema_s = _ema(close, ema_slow)
        rsi   = _rsi(close, 14)

        cross_up   = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
        cross_down = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))

        buy_entries  = cross_up   & rsi.between(rsi_low, rsi_high)
        sell_entries = cross_down & rsi.between(rsi_low - 5, rsi_high - 5)

        buy_exits  = cross_down | (rsi > 75)
        sell_exits = cross_up   | (rsi < 25)

        stats = self._run_sim(close, buy_entries, sell_entries, buy_exits, sell_exits, init_cash)
        period_days = int((df.index[-1] - df.index[0]).days) if len(df) > 1 else 0

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": "scalping",
            "period_days": period_days,
            **stats,
        }

    def compare_strategies(
        self,
        symbol: str,
        init_cash: float = 10_000.0,
    ) -> dict[str, Any]:
        """Run both swing (H4) and scalping (M5) for the same symbol.

        Returns a side-by-side comparison dict.
        """
        swing   = self.run_swing_backtest(symbol, "H4", init_cash=init_cash)
        scalping = self.run_scalping_backtest(symbol, "M5", init_cash=init_cash)
        return {"symbol": symbol, "swing": swing, "scalping": scalping}

    def run_all_pairs(
        self,
        strategy: str = "swing",
        init_cash: float = 10_000.0,
    ) -> list[dict[str, Any]]:
        """Run backtests for every pair in the watchlist.

        Returns a list of stats dicts sorted by net_pnl descending.
        """
        results: list[dict] = []
        for symbol in self._watchlist:
            try:
                if strategy == "scalping":
                    stats = self.run_scalping_backtest(symbol, init_cash=init_cash)
                else:
                    stats = self.run_swing_backtest(symbol, init_cash=init_cash)
                results.append(stats)
            except (DataLoadError, Exception) as exc:
                logger.warning(f"[BacktestEngine] Skipping {symbol}: {exc}")
                results.append({
                    "symbol": symbol,
                    "strategy": strategy,
                    "error": str(exc),
                    "net_pnl": 0,
                    "sharpe_ratio": 0,
                })

        results.sort(key=lambda x: x.get("net_pnl", 0), reverse=True)
        return results

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_data(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            return self._loader.load(symbol, timeframe, count)
        except DataLoadError:
            raise

    def _run_sim(
        self,
        close: pd.Series,
        buy_entries: pd.Series,
        sell_entries: pd.Series,
        buy_exits: pd.Series,
        sell_exits: pd.Series,
        init_cash: float,
    ) -> dict[str, Any]:
        if VBT_AVAILABLE and vbt is not None:
            return self._run_vbt(close, buy_entries, buy_exits, init_cash)
        # Pandas fallback — run buy side only (long-only backtest)
        all_entries = buy_entries | sell_entries
        all_exits   = buy_exits   | sell_exits
        return _pandas_simulate(close, all_entries, all_exits, init_cash)

    @staticmethod
    def _run_vbt(
        close: pd.Series,
        entries: pd.Series,
        exits: pd.Series,
        init_cash: float,
    ) -> dict[str, Any]:
        """Run simulation using vectorbt Portfolio.from_signals."""
        pf = vbt.Portfolio.from_signals(
            close,
            entries=entries,
            exits=exits,
            init_cash=init_cash,
            fees=0.0001,
        )
        stats = pf.stats()

        trades_obj = pf.trades
        trade_returns = list(trades_obj.returns.values) if len(trades_obj.records) > 0 else []
        wins = [r for r in trade_returns if r > 0]
        losses = [r for r in trade_returns if r <= 0]

        return {
            "total_trades": int(stats.get("Total Trades", 0)),
            "win_rate": round(float(stats.get("Win Rate [%]", 0)) / 100, 4),
            "net_pnl": round(float(stats.get("Total Profit", stats.get("Total Return [$]", 0))), 2),
            "profit_factor": round(
                sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0, 3
            ),
            "max_drawdown_pct": round(float(stats.get("Max Drawdown [%]", 0)), 2),
            "sharpe_ratio": round(float(stats.get("Sharpe Ratio", 0)), 3),
            "avg_trade_duration_hours": 0,
            "best_trade_pct": round(max(wins) * 100 if wins else 0, 2),
            "worst_trade_pct": round(min(losses) * 100 if losses else 0, 2),
            "total_return_pct": round(float(stats.get("Total Return [%]", 0)), 2),
            "trade_returns": [round(r * 100, 4) for r in trade_returns],
        }
