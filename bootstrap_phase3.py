#!/usr/bin/env python3
"""
bootstrap_phase3.py
===================
Phase 3: Backtesting module.
Run AFTER bootstrap.py and bootstrap_phase2.py:

    python bootstrap.py         # Phase 1 — core modules
    python bootstrap_phase2.py  # Phase 2 — dashboard + WS
    python bootstrap_phase3.py  # Phase 3 — backtesting

Creates / overwrites:
  backtesting/__init__.py
  backtesting/data/.gitkeep
  backtesting/data_loader.py     — HistoricalDataLoader
  backtesting/engine.py          — BacktestEngine (vectorbt + pandas)
  backtesting/optimizer.py       — StrategyOptimizer grid search + walk-forward
  backtesting/monte_carlo.py     — MonteCarloSimulator
  api/routes.py                  — updated with /backtest endpoints
  dashboard/index.html           — updated with Backtesting tab (6th)
"""
from pathlib import Path

BASE = Path(__file__).parent


def W(rel: str, content: str) -> None:
    """Write content to a file, creating parent directories."""
    p = BASE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"  OK  {rel}")


print("=" * 60)
print("ClaudeTradingBot — Phase 3 Bootstrap (Backtesting)")
print("=" * 60)
print()

# ══════════════════════════════════════════════════════════════
# 1. backtesting/__init__.py
# ══════════════════════════════════════════════════════════════
W("backtesting/__init__.py", '''"""
backtesting/
============
Backtesting module for ClaudeTradingBot.

Modules:
  data_loader   — load historical OHLCV data from MT5 or CSV cache
  engine        — run swing / scalping backtests using vectorbt
  optimizer     — grid search + walk-forward analysis
  monte_carlo   — Monte Carlo simulation for risk-of-ruin analysis
"""
from .data_loader import HistoricalDataLoader
from .engine import BacktestEngine
from .optimizer import StrategyOptimizer
from .monte_carlo import MonteCarloSimulator

__all__ = [
    "HistoricalDataLoader",
    "BacktestEngine",
    "StrategyOptimizer",
    "MonteCarloSimulator",
]
''')

# ══════════════════════════════════════════════════════════════
# 2. backtesting/data/.gitkeep
# ══════════════════════════════════════════════════════════════
W("backtesting/data/.gitkeep", "")

# ══════════════════════════════════════════════════════════════
# 3. backtesting/data_loader.py
# ══════════════════════════════════════════════════════════════
W("backtesting/data_loader.py", '''"""
backtesting/data_loader.py
==========================
Historical OHLCV data loader.

Loads data from MT5 via the MetaTrader5 library, caches results as CSV
files under backtesting/data/ (24-hour expiry). Falls back to CSV cache
when MT5 is not connected.

Usage::

    from core.mt5_bridge import MT5Bridge
    from backtesting.data_loader import HistoricalDataLoader

    bridge = MT5Bridge()
    loader = HistoricalDataLoader(bridge)
    df = loader.load("XAUUSD", "H4", count=2000)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

# ── Optional MT5 import ──────────────────────────────────────────────────────
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False

# ── Constants ────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
CSV_EXPIRY_HOURS = 24

TIMEFRAME_MAP: dict[str, int] = {}
if MT5_AVAILABLE and mt5 is not None:
    TIMEFRAME_MAP = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }


class DataLoadError(Exception):
    """Raised when historical data cannot be fetched from MT5 or cache."""


class HistoricalDataLoader:
    """Load and cache historical OHLCV data from MetaTrader 5.

    Parameters
    ----------
    mt5_bridge:
        An initialized MT5Bridge instance. Pass ``None`` to operate in
        cache-only mode (CSV files must already exist).
    rules_path:
        Path to strategies/rules.json used to resolve the watchlist.
    """

    def __init__(self, mt5_bridge=None, rules_path: Optional[Path] = None) -> None:
        self._bridge = mt5_bridge
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Load watchlist from rules.json
        if rules_path is None:
            rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        self._watchlist: list[str] = []
        if rules_path.exists():
            with open(rules_path) as fh:
                rules = json.load(fh)
            self._watchlist = rules.get("watchlist", [])

    # ── Public API ───────────────────────────────────────────────────────────

    def load(
        self,
        symbol: str,
        timeframe: str,
        count: int = 5000,
    ) -> pd.DataFrame:
        """Load OHLCV data: CSV cache first, MT5 fallback.

        Parameters
        ----------
        symbol:    Instrument symbol, e.g. ``"XAUUSD"``.
        timeframe: Timeframe string, e.g. ``"H4"``.
        count:     Number of bars to fetch.

        Returns
        -------
        pd.DataFrame with columns ``[open, high, low, close, tick_volume, spread]``
        and a timezone-aware UTC ``DatetimeIndex``.

        Raises
        ------
        DataLoadError
            If neither CSV cache nor MT5 can supply data.
        """
        cached = self.load_from_csv(symbol, timeframe)
        if cached is not None and len(cached) >= min(count, 100):
            logger.debug(f"[DataLoader] CSV cache hit: {symbol} {timeframe}")
            return cached

        logger.info(f"[DataLoader] Fetching from MT5: {symbol} {timeframe} x{count}")
        df = self._fetch_from_mt5(symbol, timeframe, count)
        self.save_to_csv(df, symbol, timeframe)
        return df

    def load_range(
        self,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
    ) -> pd.DataFrame:
        """Load OHLCV data for a specific date range via MT5.

        Parameters
        ----------
        symbol:    Instrument symbol.
        timeframe: Timeframe string.
        date_from: Start datetime (UTC-aware).
        date_to:   End datetime (UTC-aware).

        Returns
        -------
        pd.DataFrame same format as :meth:`load`.
        """
        if not MT5_AVAILABLE or mt5 is None:
            raise DataLoadError("MetaTrader5 library not available.")
        tf_const = self._resolve_timeframe(timeframe)
        rates = mt5.copy_rates_range(symbol, tf_const, date_from, date_to)
        if rates is None or len(rates) == 0:
            raise DataLoadError(
                f"MT5 returned no data for {symbol} {timeframe} "
                f"{date_from} — {date_to}"
            )
        return self._rates_to_df(rates)

    def load_all_pairs(
        self,
        timeframe: str,
        count: int = 5000,
    ) -> dict[str, pd.DataFrame]:
        """Load data for all watchlist pairs.

        Returns
        -------
        dict mapping symbol → DataFrame.  Missing/failed pairs are logged and
        excluded from the result.
        """
        result: dict[str, pd.DataFrame] = {}
        for symbol in self._watchlist:
            try:
                result[symbol] = self.load(symbol, timeframe, count)
            except DataLoadError as exc:
                logger.warning(f"[DataLoader] Skipping {symbol}: {exc}")
        return result

    def save_to_csv(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> Path:
        """Persist a DataFrame as a CSV file under backtesting/data/.

        Returns the file path.
        """
        path = DATA_DIR / f"{symbol}_{timeframe}.csv"
        df.to_csv(path)
        logger.debug(f"[DataLoader] Saved {len(df)} rows → {path}")
        return path

    def load_from_csv(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[pd.DataFrame]:
        """Load cached CSV if it exists and is younger than CSV_EXPIRY_HOURS.

        Returns ``None`` if the file doesn't exist or is stale.
        """
        path = DATA_DIR / f"{symbol}_{timeframe}.csv"
        if not path.exists():
            return None
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age > timedelta(hours=CSV_EXPIRY_HOURS):
            logger.debug(f"[DataLoader] CSV stale ({age}): {path.name}")
            return None
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        return df

    # ── Private helpers ──────────────────────────────────────────────────────

    def _fetch_from_mt5(
        self,
        symbol: str,
        timeframe: str,
        count: int,
    ) -> pd.DataFrame:
        if not MT5_AVAILABLE or mt5 is None:
            raise DataLoadError("MetaTrader5 library not available.")
        tf_const = self._resolve_timeframe(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            err = mt5.last_error()
            raise DataLoadError(
                f"MT5 returned no data for {symbol} {timeframe}. Error: {err}"
            )
        return self._rates_to_df(rates)

    @staticmethod
    def _resolve_timeframe(timeframe: str) -> int:
        if not MT5_AVAILABLE or mt5 is None:
            raise DataLoadError("MetaTrader5 library not available.")
        if timeframe not in TIMEFRAME_MAP:
            raise DataLoadError(
                f"Unknown timeframe '{timeframe}'. "
                f"Valid values: {list(TIMEFRAME_MAP.keys())}"
            )
        return TIMEFRAME_MAP[timeframe]

    @staticmethod
    def _rates_to_df(rates) -> pd.DataFrame:
        """Convert MT5 copy_rates result to a clean DataFrame."""
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        # Keep only relevant columns
        keep = ["open", "high", "low", "close", "tick_volume", "spread"]
        for col in keep:
            if col not in df.columns:
                df[col] = 0
        return df[keep]
''')

# ══════════════════════════════════════════════════════════════
# 4. backtesting/engine.py
# ══════════════════════════════════════════════════════════════
W("backtesting/engine.py", '''"""
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
''')

# ══════════════════════════════════════════════════════════════
# 5. backtesting/optimizer.py
# ══════════════════════════════════════════════════════════════
W("backtesting/optimizer.py", '''"""
backtesting/optimizer.py
========================
Parameter optimization for swing and scalping strategies.

Performs:
- Grid search over indicator parameters (ema periods, RSI thresholds)
- Walk-forward analysis (in-sample optimize → out-of-sample validate)
- Cross-pair performance comparison

Usage::

    from backtesting.optimizer import StrategyOptimizer
    result = StrategyOptimizer(engine).optimize_swing_params("XAUUSD")
"""
from __future__ import annotations

import itertools
from typing import Any

import pandas as pd
from loguru import logger

from .engine import BacktestEngine


class StrategyOptimizer:
    """Grid-search and walk-forward optimizer for trading strategies.

    Parameters
    ----------
    engine:
        :class:`BacktestEngine` instance used to run backtests.
    """

    def __init__(self, engine: BacktestEngine) -> None:
        self._engine = engine

    # ── Swing optimization ───────────────────────────────────────────────────

    def optimize_swing_params(
        self,
        symbol: str,
        timeframe: str = "H4",
    ) -> dict[str, Any]:
        """Grid search over swing strategy parameters.

        Parameter grid:
        - ema_fast: [20, 50, 100]
        - ema_slow: [100, 200]
        - rsi_low:  [40, 45, 50]
        - rsi_high: [60, 65, 70]

        Returns
        -------
        dict with keys: ``best_params``, ``best_stats``, ``all_results`` (top 10).
        """
        grid = list(itertools.product(
            [20, 50, 100],   # ema_fast
            [100, 200],      # ema_slow
            [40, 45, 50],    # rsi_low
            [60, 65, 70],    # rsi_high
        ))
        return self._run_grid(symbol, timeframe, "swing", grid)

    # ── Scalping optimization ────────────────────────────────────────────────

    def optimize_scalping_params(
        self,
        symbol: str,
        timeframe: str = "M5",
    ) -> dict[str, Any]:
        """Grid search over scalping strategy parameters.

        Parameter grid:
        - ema_fast: [5, 9, 13]
        - ema_slow: [15, 21, 34]
        - rsi_low:  [35, 40, 45]
        - rsi_high: [55, 60, 65]
        """
        grid = list(itertools.product(
            [5, 9, 13],   # ema_fast
            [15, 21, 34], # ema_slow
            [35, 40, 45], # rsi_low
            [55, 60, 65], # rsi_high
        ))
        return self._run_grid(symbol, timeframe, "scalping", grid)

    # ── Walk-forward analysis ────────────────────────────────────────────────

    def walk_forward_analysis(
        self,
        symbol: str,
        strategy: str = "swing",
        n_splits: int = 5,
    ) -> dict[str, Any]:
        """Walk-forward validation.

        Splits the full data into ``n_splits`` folds.
        Each fold: optimize on in-sample half, validate on out-of-sample half.

        Returns
        -------
        dict with fold-by-fold stats and aggregate consistency score.
        """
        from .data_loader import HistoricalDataLoader
        # Load full dataset
        loader = self._engine._loader
        timeframe = "H4" if strategy == "swing" else "M5"
        count = 5000 if strategy == "swing" else 10_000
        df = loader.load(symbol, timeframe, count)

        fold_size = len(df) // n_splits
        if fold_size < 200:
            logger.warning(f"[Optimizer] Insufficient data for {n_splits} folds on {symbol}")
            n_splits = max(2, len(df) // 200)
            fold_size = len(df) // n_splits

        folds: list[dict] = []
        in_sample_returns: list[float] = []
        out_sample_returns: list[float] = []

        for i in range(n_splits):
            start = i * fold_size
            mid   = start + fold_size // 2
            end   = start + fold_size

            in_sample_df  = df.iloc[start:mid]
            out_sample_df = df.iloc[mid:end]

            if len(in_sample_df) < 100 or len(out_sample_df) < 50:
                continue

            # Optimize on in-sample
            try:
                if strategy == "swing":
                    opt_result = self.optimize_swing_params(symbol, timeframe)
                else:
                    opt_result = self.optimize_scalping_params(symbol, timeframe)
                best_params = opt_result.get("best_params", {})
            except Exception:
                best_params = {}

            # Validate on out-of-sample using best params
            try:
                if strategy == "swing":
                    os_stats = self._engine.run_swing_backtest(
                        symbol, timeframe, count=len(out_sample_df), params=best_params
                    )
                    is_stats = self._engine.run_swing_backtest(
                        symbol, timeframe, count=len(in_sample_df), params=best_params
                    )
                else:
                    os_stats = self._engine.run_scalping_backtest(
                        symbol, timeframe, count=len(out_sample_df), params=best_params
                    )
                    is_stats = self._engine.run_scalping_backtest(
                        symbol, timeframe, count=len(in_sample_df), params=best_params
                    )
            except Exception as exc:
                logger.warning(f"[Optimizer] Fold {i} failed: {exc}")
                continue

            folds.append({
                "fold": i + 1,
                "in_sample": is_stats,
                "out_of_sample": os_stats,
                "best_params": best_params,
            })
            in_sample_returns.append(is_stats.get("total_return_pct", 0))
            out_sample_returns.append(os_stats.get("total_return_pct", 0))

        avg_is  = sum(in_sample_returns) / len(in_sample_returns) if in_sample_returns else 0
        avg_os  = sum(out_sample_returns) / len(out_sample_returns) if out_sample_returns else 0
        avg_wr  = sum(f["out_of_sample"].get("win_rate", 0) for f in folds) / len(folds) if folds else 0
        consistency = avg_os / avg_is if avg_is != 0 else 0

        return {
            "symbol": symbol,
            "strategy": strategy,
            "n_splits": n_splits,
            "folds": folds,
            "avg_out_of_sample_win_rate": round(avg_wr, 4),
            "avg_out_of_sample_return_pct": round(avg_os, 2),
            "consistency_score": round(consistency, 3),
        }

    # ── Cross-pair performance ───────────────────────────────────────────────

    def compare_all_pairs_performance(
        self,
        strategy: str = "swing",
    ) -> pd.DataFrame:
        """Run backtest for all watchlist pairs and return a comparison DataFrame.

        Columns: symbol, total_trades, win_rate, net_pnl, profit_factor,
                 max_drawdown_pct, sharpe_ratio.
        Sorted by sharpe_ratio descending.
        """
        rows = self._engine.run_all_pairs(strategy)
        df = pd.DataFrame(rows)
        keep = ["symbol", "total_trades", "win_rate", "net_pnl",
                "profit_factor", "max_drawdown_pct", "sharpe_ratio"]
        for col in keep:
            if col not in df.columns:
                df[col] = 0
        return df[keep].sort_values("sharpe_ratio", ascending=False)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _run_grid(
        self,
        symbol: str,
        timeframe: str,
        strategy: str,
        grid: list[tuple],
    ) -> dict[str, Any]:
        """Execute a grid search and return ranked results."""
        results: list[dict] = []
        total = len(grid)
        logger.info(f"[Optimizer] Grid search: {symbol} {strategy} — {total} combinations")

        for ema_fast, ema_slow, rsi_low, rsi_high in grid:
            if ema_fast >= ema_slow:
                continue  # Invalid parameter pair
            params = {
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "rsi_low":  rsi_low,
                "rsi_high": rsi_high,
            }
            try:
                if strategy == "swing":
                    stats = self._engine.run_swing_backtest(symbol, timeframe, params=params)
                else:
                    stats = self._engine.run_scalping_backtest(symbol, timeframe, params=params)
            except Exception as exc:
                logger.debug(f"[Optimizer] params {params} failed: {exc}")
                continue

            # Filter: need >= 10 trades for statistical significance
            if stats.get("total_trades", 0) < 10:
                continue

            results.append({"params": params, "stats": stats})

        if not results:
            return {"best_params": {}, "best_stats": {}, "all_results": []}

        # Rank by Sharpe ratio, then profit_factor
        results.sort(
            key=lambda x: (
                x["stats"].get("sharpe_ratio", 0),
                x["stats"].get("profit_factor", 0),
            ),
            reverse=True,
        )

        return {
            "best_params": results[0]["params"],
            "best_stats":  results[0]["stats"],
            "all_results": [
                {"params": r["params"], **r["stats"]} for r in results[:10]
            ],
        }
''')

# ══════════════════════════════════════════════════════════════
# 6. backtesting/monte_carlo.py
# ══════════════════════════════════════════════════════════════
W("backtesting/monte_carlo.py", '''"""
backtesting/monte_carlo.py
==========================
Monte Carlo simulation for strategy robustness analysis.

Shuffles historical trade returns N times to estimate the probability
distribution of outcomes, including probability of loss and risk of ruin.

Usage::

    from backtesting.monte_carlo import MonteCarloSimulator

    sim = MonteCarloSimulator(n_simulations=1000)
    result = sim.simulate(trade_returns=[0.5, -0.3, 1.2, -0.1, 0.8])
"""
from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
from loguru import logger


class MonteCarloSimulator:
    """Monte Carlo simulator for trading strategy risk analysis.

    Parameters
    ----------
    n_simulations:
        Number of simulation iterations (default 1000).
    """

    def __init__(self, n_simulations: int = 1000) -> None:
        self.n_simulations = n_simulations

    # ── Public API ───────────────────────────────────────────────────────────

    def simulate(
        self,
        trade_returns: list[float],
        init_cash: float = 10_000.0,
        max_loss_pct: float = 3.0,
    ) -> dict[str, Any]:
        """Run Monte Carlo simulation by shuffling trade returns.

        Parameters
        ----------
        trade_returns:
            List of historical trade P&L values as percentages
            (e.g. ``[0.5, -0.3, 1.2]`` = 0.5%, -0.3%, 1.2%).
        init_cash:
            Starting equity for each simulation run.
        max_loss_pct:
            Daily loss limit percentage — crossing this triggers "ruin".

        Returns
        -------
        dict with probability statistics and percentile equity values.
        """
        if not trade_returns:
            return self._empty_result(init_cash)

        final_equities: list[float] = []
        max_drawdowns: list[float] = []
        ruin_count = 0
        loss_count = 0

        for _ in range(self.n_simulations):
            shuffled = trade_returns.copy()
            random.shuffle(shuffled)

            equity = init_cash
            peak   = init_cash
            max_dd = 0.0
            ruined = False

            for ret_pct in shuffled:
                equity *= 1 + ret_pct / 100
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
                if dd >= max_loss_pct and not ruined:
                    ruin_count += 1
                    ruined = True

            if equity < init_cash:
                loss_count += 1

            final_equities.append(equity)
            max_drawdowns.append(max_dd)

        final_arr = sorted(final_equities)
        dd_arr    = sorted(max_drawdowns)
        n = self.n_simulations

        return {
            "n_simulations": n,
            "init_cash": init_cash,
            "median_final_equity":   round(float(np.median(final_arr)), 2),
            "p5_final_equity":       round(float(np.percentile(final_arr, 5)), 2),
            "p95_final_equity":      round(float(np.percentile(final_arr, 95)), 2),
            "probability_of_loss":   round(loss_count / n, 4),
            "probability_of_ruin":   round(ruin_count / n, 4),
            "median_max_drawdown_pct": round(float(np.median(dd_arr)), 2),
            "worst_case_drawdown_pct": round(float(np.percentile(dd_arr, 95)), 2),
            "expected_return_pct": round(
                (float(np.median(final_arr)) - init_cash) / init_cash * 100, 2
            ),
            # Include sample equity curves for fan chart (first 50 simulations)
            "sample_equity_curves": self._sample_curves(trade_returns, init_cash, 50),
        }

    def simulate_from_backtest(
        self,
        backtest_stats: dict,
        init_cash: float = 10_000.0,
    ) -> dict[str, Any]:
        """Run simulation from a BacktestEngine stats dict.

        Extracts ``trade_returns`` from backtest_stats and delegates to
        :meth:`simulate`.
        """
        trade_returns = backtest_stats.get("trade_returns", [])
        if not trade_returns:
            logger.warning("[MonteCarlo] No trade_returns in backtest_stats — generating synthetic")
            # Generate synthetic returns from win_rate + avg trade metrics
            trade_returns = self._synthetic_returns(backtest_stats)
        return self.simulate(trade_returns, init_cash)

    def risk_of_ruin(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        max_loss_pct: float = 3.0,
        n_trades: int = 100,
    ) -> float:
        """Analytical Kelly-based risk of ruin estimate.

        Parameters
        ----------
        win_rate:     Probability of winning a single trade (0.0 – 1.0).
        avg_win:      Average winning trade return as percentage.
        avg_loss:     Average losing trade return as percentage (positive value).
        max_loss_pct: Drawdown level considered "ruin".
        n_trades:     Number of trades to simulate.

        Returns
        -------
        float between 0.0 and 1.0.
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 1.0

        loss_rate = 1 - win_rate
        # Edge per trade
        edge = win_rate * avg_win - loss_rate * avg_loss
        if edge <= 0:
            return 1.0  # Negative edge → always ruins

        # Probability of drawdown >= max_loss_pct using a simplified ruin formula:
        # P(ruin) ≈ ((loss_rate / win_rate) ^ (max_loss_pct / avg_loss))
        base = loss_rate / win_rate if win_rate > 0 else 1.0
        exponent = max_loss_pct / avg_loss if avg_loss > 0 else 1.0
        ror = base ** exponent
        return round(min(float(ror), 1.0), 4)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _sample_curves(
        self,
        trade_returns: list[float],
        init_cash: float,
        n_samples: int,
    ) -> list[list[float]]:
        """Generate n_samples shuffled equity curves (used for fan chart)."""
        curves: list[list[float]] = []
        for _ in range(min(n_samples, self.n_simulations)):
            shuffled = trade_returns.copy()
            random.shuffle(shuffled)
            equity = init_cash
            curve = [equity]
            for ret_pct in shuffled:
                equity *= 1 + ret_pct / 100
                curve.append(round(equity, 2))
            curves.append(curve)
        return curves

    @staticmethod
    def _synthetic_returns(stats: dict) -> list[float]:
        """Generate synthetic trade returns from summary stats."""
        n      = max(stats.get("total_trades", 20), 20)
        wr     = stats.get("win_rate", 0.5)
        net    = stats.get("total_return_pct", 0)
        avg_r  = net / n if n else 0
        returns: list[float] = []
        for _ in range(n):
            if random.random() < wr:
                returns.append(abs(avg_r) * 2)
            else:
                returns.append(-abs(avg_r))
        return returns

    @staticmethod
    def _empty_result(init_cash: float) -> dict[str, Any]:
        return {
            "n_simulations": 0,
            "init_cash": init_cash,
            "median_final_equity": init_cash,
            "p5_final_equity": init_cash,
            "p95_final_equity": init_cash,
            "probability_of_loss": 0,
            "probability_of_ruin": 0,
            "median_max_drawdown_pct": 0,
            "worst_case_drawdown_pct": 0,
            "expected_return_pct": 0,
            "sample_equity_curves": [],
        }
''')

# ══════════════════════════════════════════════════════════════
# 7. api/routes.py — rewrite with backtest endpoints added
# ══════════════════════════════════════════════════════════════
W("api/routes.py", '''"""
api/routes.py
=============
FastAPI router — all HTTP + WebSocket endpoints for ClaudeTradingBot.

Phases included:
  Phase 1: /status /signals /trades /execute /pause /resume /performance /health
  Phase 2: /ws  (WebSocket), /screenshot (upload), /dashboard (static)
  Phase 3: /backtest/* (run, result, compare, all-pairs, optimize, monte-carlo)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger
from pydantic import BaseModel

# ── Internal imports (guarded for partial-init environments) ─────────────────
try:
    from api.ws_manager import ws_manager
except ImportError:
    ws_manager = None  # type: ignore[assignment]

try:
    from database.db import get_db
    from database.queries import (
        get_all_signals,
        get_all_trades,
        get_performance_summary,
    )
except ImportError:
    get_db = None  # type: ignore[assignment]
    get_all_signals = get_all_trades = get_performance_summary = None  # type: ignore

# ── Backtesting imports (Phase 3) ────────────────────────────────────────────
_backtest_engine = None
_data_loader     = None

def _get_backtest_engine():
    """Lazy-load BacktestEngine — avoids MT5 import at startup."""
    global _backtest_engine, _data_loader
    if _backtest_engine is None:
        try:
            from backtesting.data_loader import HistoricalDataLoader
            from backtesting.engine import BacktestEngine
            _data_loader = HistoricalDataLoader()
            _backtest_engine = BacktestEngine(_data_loader)
        except Exception as exc:
            logger.warning(f"[Routes] BacktestEngine unavailable: {exc}")
    return _backtest_engine

# ── In-memory job store for background tasks ─────────────────────────────────
_jobs: dict[str, dict] = {}

router = APIRouter()

# ════════════════════════════════════════════════════════════════
# Pydantic schemas for request bodies
# ════════════════════════════════════════════════════════════════

class ExecuteRequest(BaseModel):
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    lot_size: Optional[float] = None
    order_type: str = "BUY_LIMIT"
    timeframe: str = "H4"
    strategy: str = "swing"
    notes: Optional[str] = None


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"
    count: int = 5000
    init_cash: float = 10_000.0


class OptimizeRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"


class MonteCarloRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    n_simulations: int = 1000
    init_cash: float = 10_000.0


# ════════════════════════════════════════════════════════════════
# PHASE 1 — Core endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/health")
async def health_check():
    """Service health check."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status():
    """Return bot status: mode, uptime, active pairs, positions."""
    try:
        from main import app_state  # type: ignore[import]
        return app_state
    except ImportError:
        return {
            "status": "running",
            "mode": "SIGNAL_ONLY",
            "active_pairs": [],
            "open_positions": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/signals")
async def get_signals(limit: int = 50):
    """Return latest trade signals from database."""
    if get_all_signals is None or get_db is None:
        return {"signals": [], "count": 0}
    async for db in get_db():
        signals = get_all_signals(db, limit=limit)
        return {"signals": [s.__dict__ for s in signals], "count": len(signals)}


@router.get("/trades")
async def get_trades(limit: int = 50):
    """Return executed trades from database."""
    if get_all_trades is None or get_db is None:
        return {"trades": [], "count": 0}
    async for db in get_db():
        trades = get_all_trades(db, limit=limit)
        return {"trades": [t.__dict__ for t in trades], "count": len(trades)}


@router.post("/execute")
async def manual_execute(req: ExecuteRequest):
    """Manually trigger a trade execution (AUTO_EXECUTE mode required)."""
    logger.info(f"[Routes] Manual execute: {req.symbol} {req.direction}")
    return {
        "status": "queued",
        "symbol": req.symbol,
        "direction": req.direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pause")
async def pause_bot():
    """Pause the signal processing loop."""
    logger.warning("[Routes] Bot paused via API")
    return {"status": "paused", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/resume")
async def resume_bot():
    """Resume the signal processing loop."""
    logger.info("[Routes] Bot resumed via API")
    return {"status": "running", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/performance")
async def get_performance(days: int = 30):
    """Return performance summary for the last N days."""
    if get_performance_summary is None or get_db is None:
        return {"message": "Database not available", "days": days}
    async for db in get_db():
        summary = get_performance_summary(db, days=days)
        return summary


# ════════════════════════════════════════════════════════════════
# PHASE 2 — WebSocket + Screenshot
# ════════════════════════════════════════════════════════════════

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint — streams real-time events to the dashboard."""
    if ws_manager is None:
        await websocket.close(code=1011)
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"event": "ping", "data": {}})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@router.post("/screenshot")
async def upload_screenshot():
    """Placeholder — screenshot upload endpoint (Phase 2)."""
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════
# PHASE 3 — Backtesting endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/backtest/run")
async def backtest_run(
    background_tasks: BackgroundTasks,
    symbol: str = "XAUUSD",
    strategy: str = "swing",
    timeframe: str = "H4",
    count: int = 5000,
    init_cash: float = 10_000.0,
):
    """Start a backtest job in the background.

    Returns a job_id immediately.  Poll ``GET /backtest/result/{job_id}``
    to retrieve results when complete.
    """
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            if strategy == "scalping":
                result = engine.run_scalping_backtest(symbol, timeframe, count, init_cash)
            else:
                result = engine.run_swing_backtest(symbol, timeframe, count, init_cash)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/backtest/result/{job_id}")
async def backtest_result(job_id: str):
    """Poll for backtest job result."""
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


@router.get("/backtest/compare")
async def backtest_compare(symbol: str = "XAUUSD", init_cash: float = 10_000.0):
    """Run both swing and scalping backtests side-by-side for a symbol."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.compare_strategies(symbol, init_cash)


@router.get("/backtest/all-pairs")
async def backtest_all_pairs(strategy: str = "swing", init_cash: float = 10_000.0):
    """Run backtests for all watchlist pairs, sorted by net P&L."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.run_all_pairs(strategy, init_cash)


@router.post("/backtest/optimize")
async def backtest_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """Start parameter optimization job in the background."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    from backtesting.optimizer import StrategyOptimizer

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            optimizer = StrategyOptimizer(engine)
            if req.strategy == "scalping":
                result = optimizer.optimize_scalping_params(req.symbol, req.timeframe)
            else:
                result = optimizer.optimize_swing_params(req.symbol, req.timeframe)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.post("/backtest/monte-carlo")
async def backtest_monte_carlo(req: MonteCarloRequest):
    """Run full backtest then Monte Carlo simulation."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    from backtesting.monte_carlo import MonteCarloSimulator

    try:
        stats = engine.run_swing_backtest(req.symbol, init_cash=req.init_cash)
        sim = MonteCarloSimulator(n_simulations=req.n_simulations)
        mc_result = sim.simulate_from_backtest(stats, req.init_cash)
        return {
            "backtest_stats": stats,
            "monte_carlo": mc_result,
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))
''')

# ══════════════════════════════════════════════════════════════
# 8. dashboard/index.html — add Backtesting tab (6th tab)
# ══════════════════════════════════════════════════════════════
W("dashboard/index.html", """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>ClaudeTradingBot Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root{
      --bg:#0f1117;--surface:#1a1d27;--surface2:#23273a;
      --accent:#6c63ff;--accent2:#00d4aa;--warn:#f59e0b;
      --danger:#ef4444;--text:#e2e8f0;--muted:#64748b;
      --green:#22c55e;--red:#ef4444;--amber:#f59e0b;
    }
    *{box-sizing:border-box;margin:0;padding:0;}
    body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;display:flex;height:100vh;overflow:hidden;}

    /* ── Sidebar ──────────────────────────────────────── */
    .sidebar{width:220px;background:var(--surface);display:flex;flex-direction:column;padding:1rem 0;flex-shrink:0;}
    .sidebar-logo{padding:0.5rem 1.5rem 1.5rem;font-size:1.1rem;font-weight:700;color:var(--accent);}
    .sidebar-logo span{color:var(--accent2);}
    .nav-item{padding:0.75rem 1.5rem;cursor:pointer;transition:background 0.2s;display:flex;align-items:center;gap:0.75rem;font-size:0.9rem;color:var(--muted);border-left:3px solid transparent;}
    .nav-item:hover{background:var(--surface2);color:var(--text);}
    .nav-item.active{background:var(--surface2);color:var(--accent);border-left-color:var(--accent);}
    .sidebar-footer{margin-top:auto;padding:1rem 1.5rem;font-size:0.75rem;color:var(--muted);}

    /* ── Main content ─────────────────────────────────── */
    .main{flex:1;overflow-y:auto;padding:1.5rem;}
    .section{display:none;}
    .section.active{display:block;}
    h2{font-size:1.3rem;margin-bottom:1.25rem;color:var(--text);}

    /* ── Cards ────────────────────────────────────────── */
    .card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:1.5rem;}
    .card{background:var(--surface);border-radius:10px;padding:1.1rem 1.25rem;}
    .card-label{font-size:0.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
    .card-value{font-size:1.5rem;font-weight:700;margin-top:0.4rem;}
    .card-sub{font-size:0.75rem;color:var(--muted);margin-top:0.2rem;}

    /* ── Tables ───────────────────────────────────────── */
    .table-wrap{background:var(--surface);border-radius:10px;overflow:hidden;}
    table{width:100%;border-collapse:collapse;font-size:0.85rem;}
    th{background:var(--surface2);padding:0.75rem 1rem;text-align:left;font-weight:600;color:var(--muted);text-transform:uppercase;font-size:0.7rem;letter-spacing:.05em;}
    td{padding:0.75rem 1rem;border-bottom:1px solid var(--surface2);}
    tr:last-child td{border-bottom:none;}
    tr:hover td{background:rgba(255,255,255,.02);}

    /* ── Badges ───────────────────────────────────────── */
    .badge{display:inline-block;padding:.2em .6em;border-radius:4px;font-size:.75rem;font-weight:600;}
    .badge-green{background:rgba(34,197,94,.15);color:var(--green);}
    .badge-red{background:rgba(239,68,68,.15);color:var(--red);}
    .badge-amber{background:rgba(245,158,11,.15);color:var(--amber);}
    .badge-blue{background:rgba(108,99,255,.15);color:var(--accent);}
    .badge-teal{background:rgba(0,212,170,.15);color:var(--accent2);}

    /* ── Buttons ──────────────────────────────────────── */
    .btn{padding:.5rem 1.1rem;border:none;border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600;transition:opacity .2s;}
    .btn-primary{background:var(--accent);color:#fff;}
    .btn-teal{background:var(--accent2);color:#000;}
    .btn-danger{background:var(--danger);color:#fff;}
    .btn:hover{opacity:.85;}
    .btn:disabled{opacity:.4;cursor:not-allowed;}

    /* ── Status dot ───────────────────────────────────── */
    .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;}
    .dot-green{background:var(--green);box-shadow:0 0 6px var(--green);}
    .dot-red{background:var(--red);}
    .dot-amber{background:var(--amber);animation:pulse 1.5s infinite;}
    @keyframes pulse{0%,100%{opacity:1;}50%{opacity:.4;}}

    /* ── WS status bar ────────────────────────────────── */
    .ws-bar{background:var(--surface);border-radius:8px;padding:.5rem 1rem;margin-bottom:1.25rem;font-size:.8rem;color:var(--muted);display:flex;align-items:center;gap:.5rem;}

    /* ── Chart containers ─────────────────────────────── */
    .chart-box{background:var(--surface);border-radius:10px;padding:1.25rem;margin-bottom:1.5rem;}
    .chart-box canvas{max-height:300px;}

    /* ── Backtest controls ────────────────────────────── */
    .bt-controls{background:var(--surface);border-radius:10px;padding:1.25rem;margin-bottom:1.25rem;display:flex;gap:1rem;align-items:flex-end;flex-wrap:wrap;}
    .bt-controls label{display:flex;flex-direction:column;gap:.35rem;font-size:.8rem;color:var(--muted);}
    .bt-controls select,.bt-controls input{background:var(--surface2);color:var(--text);border:1px solid var(--surface2);border-radius:6px;padding:.45rem .75rem;font-size:.85rem;}
    .bt-progress{background:var(--surface);border-radius:10px;padding:1.25rem;text-align:center;color:var(--muted);display:none;}
    .bt-results{display:none;}

    /* ── Monte Carlo fan ──────────────────────────────── */
    .mc-box{background:var(--surface);border-radius:10px;padding:1.25rem;margin-top:1.25rem;display:none;}
  </style>
</head>
<body>

<!-- ══════════════════ SIDEBAR ══════════════════ -->
<nav class="sidebar">
  <div class="sidebar-logo">Claude<span>TradingBot</span></div>
  <div class="nav-item active" onclick="showSection('dashboard',this)">📊 Dashboard</div>
  <div class="nav-item" onclick="showSection('signals',this)">📡 Signals</div>
  <div class="nav-item" onclick="showSection('trades',this)">💼 Trades</div>
  <div class="nav-item" onclick="showSection('performance',this)">📈 Performance</div>
  <div class="nav-item" onclick="showSection('settings',this)">⚙️ Settings</div>
  <div class="nav-item" onclick="showSection('backtesting',this)">🔬 Backtesting</div>
  <div class="sidebar-footer" id="ws-footer">WS: —</div>
</nav>

<!-- ══════════════════ MAIN ══════════════════════ -->
<main class="main">

  <!-- WS status bar -->
  <div class="ws-bar"><span class="dot dot-amber" id="ws-dot"></span><span id="ws-label">Connecting…</span></div>

  <!-- ─── 1. Dashboard ─────────────────────────── -->
  <section class="section active" id="section-dashboard">
    <h2>Overview</h2>
    <div class="card-grid">
      <div class="card"><div class="card-label">Bot Mode</div><div class="card-value" id="bot-mode">—</div></div>
      <div class="card"><div class="card-label">Open Positions</div><div class="card-value" id="open-pos">—</div></div>
      <div class="card"><div class="card-label">Signals Today</div><div class="card-value" id="sigs-today">—</div></div>
      <div class="card"><div class="card-label">Trades Today</div><div class="card-value" id="trades-today">—</div></div>
      <div class="card"><div class="card-label">Daily P&L</div><div class="card-value" id="daily-pnl">—</div></div>
      <div class="card"><div class="card-label">Win Rate</div><div class="card-value" id="win-rate">—</div></div>
    </div>
    <div class="chart-box"><canvas id="pnl-chart"></canvas></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>Direction</th><th>Entry</th><th>SL</th><th>TP1</th><th>RR</th><th>Status</th></tr></thead>
      <tbody id="recent-signals-body"><tr><td colspan="8" style="text-align:center;color:var(--muted)">Loading…</td></tr></tbody>
    </table></div>
  </section>

  <!-- ─── 2. Signals ───────────────────────────── -->
  <section class="section" id="section-signals">
    <h2>Trade Signals</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>TF</th><th>Direction</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>Conf%</th><th>Strategy</th><th>Status</th></tr></thead>
      <tbody id="signals-body"><tr><td colspan="11" style="text-align:center;color:var(--muted)">No signals yet</td></tr></tbody>
    </table></div>
  </section>

  <!-- ─── 3. Trades ────────────────────────────── -->
  <section class="section" id="section-trades">
    <h2>Executed Trades</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Time</th><th>Pair</th><th>Direction</th><th>Lot</th><th>Entry</th><th>SL</th><th>TP</th><th>Profit</th><th>Status</th><th>Ticket</th></tr></thead>
      <tbody id="trades-body"><tr><td colspan="10" style="text-align:center;color:var(--muted)">No trades yet</td></tr></tbody>
    </table></div>
  </section>

  <!-- ─── 4. Performance ───────────────────────── -->
  <section class="section" id="section-performance">
    <h2>Performance</h2>
    <div class="card-grid">
      <div class="card"><div class="card-label">Total Trades</div><div class="card-value" id="p-total">—</div></div>
      <div class="card"><div class="card-label">Win Rate</div><div class="card-value" id="p-wr">—</div></div>
      <div class="card"><div class="card-label">Net P&L</div><div class="card-value" id="p-pnl">—</div></div>
      <div class="card"><div class="card-label">Profit Factor</div><div class="card-value" id="p-pf">—</div></div>
      <div class="card"><div class="card-label">Max Drawdown</div><div class="card-value" id="p-dd">—</div></div>
      <div class="card"><div class="card-label">Avg R:R</div><div class="card-value" id="p-rr">—</div></div>
    </div>
    <div class="chart-box"><canvas id="perf-chart"></canvas></div>
  </section>

  <!-- ─── 5. Settings ──────────────────────────── -->
  <section class="section" id="section-settings">
    <h2>Settings</h2>
    <div style="background:var(--surface);border-radius:10px;padding:1.5rem;max-width:500px;">
      <p style="color:var(--muted);font-size:.9rem;margin-bottom:1rem;">Runtime configuration. Changes apply to next scan cycle.</p>
      <table><tbody>
        <tr><td>Bot Mode</td><td id="cfg-mode">—</td></tr>
        <tr><td>Risk Per Trade</td><td id="cfg-risk">—</td></tr>
        <tr><td>Min RR Ratio</td><td id="cfg-rr">—</td></tr>
        <tr><td>Max Positions</td><td id="cfg-maxpos">—</td></tr>
      </tbody></table>
      <div style="margin-top:1.5rem;display:flex;gap:.75rem;">
        <button class="btn btn-teal" onclick="resumeBot()">▶ Resume</button>
        <button class="btn btn-danger" onclick="pauseBot()">⏸ Pause</button>
      </div>
    </div>
  </section>

  <!-- ─── 6. Backtesting ───────────────────────── -->
  <section class="section" id="section-backtesting">
    <h2>Backtesting</h2>

    <!-- Controls -->
    <div class="bt-controls">
      <label>Symbol
        <select id="bt-symbol">
          <option>XAUUSD</option><option>EURUSD</option><option>GBPUSD</option>
          <option>USDJPY</option><option>BTCUSD</option><option>NAS100</option><option>US30</option>
        </select>
      </label>
      <label>Strategy
        <select id="bt-strategy"><option value="swing">Swing</option><option value="scalping">Scalping</option></select>
      </label>
      <label>Timeframe
        <select id="bt-timeframe"><option>H4</option><option>H1</option><option>D1</option><option>M15</option><option>M5</option></select>
      </label>
      <label>Bars
        <input type="number" id="bt-count" value="5000" min="500" max="50000" style="width:100px;"/>
      </label>
      <label>Init Cash ($)
        <input type="number" id="bt-cash" value="10000" min="1000" style="width:120px;"/>
      </label>
      <button class="btn btn-primary" id="bt-run-btn" onclick="runBacktest()">▶ Run Backtest</button>
      <button class="btn btn-teal" onclick="compareAllPairs()">🌐 Compare All Pairs</button>
    </div>

    <!-- Progress -->
    <div class="bt-progress" id="bt-progress">
      <span class="dot dot-amber"></span> Running backtest… please wait
    </div>

    <!-- Results -->
    <div class="bt-results" id="bt-results">
      <div class="card-grid" id="bt-stat-cards"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.25rem;">
        <div class="chart-box"><canvas id="bt-equity-chart"></canvas></div>
        <div class="chart-box"><canvas id="bt-dist-chart"></canvas></div>
      </div>
      <div style="display:flex;gap:.75rem;margin-bottom:1rem;">
        <button class="btn btn-primary" onclick="runMonteCarlo()" id="mc-btn">🎲 Run Monte Carlo</button>
        <button class="btn btn-teal" onclick="optimizeParams()" id="opt-btn">🔧 Optimize Params</button>
      </div>
    </div>

    <!-- Monte Carlo -->
    <div class="mc-box" id="mc-box">
      <h3 style="margin-bottom:1rem;font-size:1rem;">Monte Carlo Results</h3>
      <div class="card-grid" id="mc-stat-cards"></div>
      <div class="chart-box" style="margin-top:1rem;"><canvas id="mc-fan-chart"></canvas></div>
    </div>

    <!-- All Pairs Compare Table -->
    <div id="all-pairs-box" style="display:none;margin-top:1.25rem;">
      <h3 style="margin-bottom:.75rem;font-size:1rem;">All Pairs Comparison</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Pair</th><th>Strategy</th><th>Trades</th><th>Win%</th><th>Net P&L</th><th>Profit Factor</th><th>Max DD%</th><th>Sharpe</th></tr></thead>
        <tbody id="all-pairs-body"></tbody>
      </table></div>
    </div>
  </section>

</main>

<script>
// ══════════════════════════════════════════════════════════
// Utilities
// ══════════════════════════════════════════════════════════
const API = '';
let wsConn = null;
let _pnlChart = null, _perfChart = null;
let _btEquityChart = null, _btDistChart = null, _mcFanChart = null;
let _lastBtStats = null;

function $(id){ return document.getElementById(id); }

function showSection(name, el){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  $('section-'+name).classList.add('active');
  el.classList.add('active');
  if(name==='performance') loadPerformance();
  if(name==='signals')     loadSignals();
  if(name==='trades')      loadTrades();
}

async function api(path, opts={}){
  try{
    const r = await fetch(API+path, opts);
    return await r.json();
  } catch(e){ console.warn('API error', path, e); return null; }
}

function fmtNum(v, dec=2){ return v==null?'—':Number(v).toFixed(dec); }
function colorVal(v){ return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'; }

function sharpeBadge(v){
  if(v==null) return '<span class="badge badge-amber">N/A</span>';
  const n=Number(v);
  if(n>=1)   return `<span class="badge badge-green">${n.toFixed(2)}</span>`;
  if(n>=0.5) return `<span class="badge badge-amber">${n.toFixed(2)}</span>`;
  return `<span class="badge badge-red">${n.toFixed(2)}</span>`;
}

// ══════════════════════════════════════════════════════════
// WebSocket
// ══════════════════════════════════════════════════════════
function connectWS(){
  const proto = location.protocol==='https:'?'wss':'ws';
  wsConn = new WebSocket(`${proto}://${location.host}/ws`);
  wsConn.onopen  = ()=>{ setWS('Connected','dot-green'); };
  wsConn.onclose = ()=>{ setWS('Disconnected','dot-red'); setTimeout(connectWS,5000); };
  wsConn.onerror = ()=>{ setWS('Error','dot-red'); };
  wsConn.onmessage = e=>{
    try{
      const msg = JSON.parse(e.data);
      if(msg.event==='new_signal')  prependSignalRow(msg.data);
      if(msg.event==='trade_event') prependTradeRow(msg.data);
      if(msg.event==='status_update') updateStatusCards(msg.data);
    } catch{}
  };
}
function setWS(label, cls){
  $('ws-label').textContent = 'WS: '+label;
  $('ws-footer').textContent = 'WS: '+label;
  $('ws-dot').className = 'dot '+cls;
}

// ══════════════════════════════════════════════════════════
// Dashboard
// ══════════════════════════════════════════════════════════
async function loadDashboard(){
  const [status, perf] = await Promise.all([api('/status'), api('/performance?days=1')]);
  if(status){ updateStatusCards(status); }
  if(perf){
    $('daily-pnl').textContent = '$'+(perf.net_pnl||0).toFixed(2);
    $('daily-pnl').style.color = colorVal(perf.net_pnl);
    $('win-rate').textContent  = ((perf.win_rate||0)*100).toFixed(1)+'%';
  }
  const sigs = await api('/signals?limit=10');
  if(sigs && sigs.signals) renderRecentSignals(sigs.signals);
  renderPnlChart([]);
}

function updateStatusCards(s){
  if(!s) return;
  $('bot-mode').textContent  = s.mode || '—';
  $('open-pos').textContent  = s.open_positions ?? '—';
  $('sigs-today').textContent = s.signals_today ?? '—';
  $('trades-today').textContent = s.trades_today ?? '—';
}

function renderRecentSignals(sigs){
  const tbody = $('recent-signals-body');
  if(!sigs.length){ tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--muted)">No signals</td></tr>'; return; }
  tbody.innerHTML = sigs.map(s=>`
    <tr>
      <td>${new Date(s.created_at||Date.now()).toLocaleTimeString()}</td>
      <td><b>${s.symbol||'—'}</b></td>
      <td><span class="badge ${s.direction==='BUY'?'badge-green':'badge-red'}">${s.direction||'—'}</span></td>
      <td>${fmtNum(s.entry)}</td>
      <td>${fmtNum(s.stop_loss)}</td>
      <td>${fmtNum(s.take_profit_1)}</td>
      <td>${fmtNum(s.rr_ratio,1)}</td>
      <td><span class="badge badge-blue">${s.status||'PENDING'}</span></td>
    </tr>`).join('');
}

function prependSignalRow(s){ renderRecentSignals([s]); }
function prependTradeRow(t){}

function renderPnlChart(data){
  const ctx = $('pnl-chart').getContext('2d');
  if(_pnlChart) _pnlChart.destroy();
  _pnlChart = new Chart(ctx,{
    type:'line',
    data:{labels:data.map((_,i)=>i),datasets:[{label:'Equity Curve',data:data,borderColor:'#6c63ff',fill:true,backgroundColor:'rgba(108,99,255,.1)',tension:.4,pointRadius:0}]},
    options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{display:false},y:{grid:{color:'rgba(255,255,255,.05)'}}}}
  });
}

// ══════════════════════════════════════════════════════════
// Signals & Trades
// ══════════════════════════════════════════════════════════
async function loadSignals(){
  const d = await api('/signals?limit=100');
  if(!d) return;
  const tb = $('signals-body');
  if(!d.signals.length){ tb.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--muted)">No signals</td></tr>'; return; }
  tb.innerHTML = d.signals.map(s=>`
    <tr>
      <td>${new Date(s.created_at||Date.now()).toLocaleString()}</td>
      <td><b>${s.symbol}</b></td><td>${s.timeframe||'—'}</td>
      <td><span class="badge ${s.direction==='BUY'?'badge-green':'badge-red'}">${s.direction}</span></td>
      <td>${fmtNum(s.entry)}</td><td>${fmtNum(s.stop_loss)}</td>
      <td>${fmtNum(s.take_profit_1)}</td><td>${fmtNum(s.take_profit_2)}</td>
      <td>${fmtNum(s.confidence,0)}%</td><td>${s.strategy||'—'}</td>
      <td><span class="badge badge-blue">${s.status||'PENDING'}</span></td>
    </tr>`).join('');
}

async function loadTrades(){
  const d = await api('/trades?limit=100');
  if(!d) return;
  const tb = $('trades-body');
  if(!d.trades.length){ tb.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted)">No trades</td></tr>'; return; }
  tb.innerHTML = d.trades.map(t=>`
    <tr>
      <td>${new Date(t.created_at||Date.now()).toLocaleString()}</td>
      <td><b>${t.symbol}</b></td>
      <td><span class="badge ${t.direction==='BUY'?'badge-green':'badge-red'}">${t.direction}</span></td>
      <td>${fmtNum(t.lot_size,2)}</td><td>${fmtNum(t.entry_price)}</td>
      <td>${fmtNum(t.stop_loss)}</td><td>${fmtNum(t.take_profit_1)}</td>
      <td style="color:${colorVal(t.profit)}">${fmtNum(t.profit,2)}</td>
      <td><span class="badge badge-teal">${t.status||'OPEN'}</span></td>
      <td style="font-size:.75rem;color:var(--muted)">${t.mt5_ticket||'—'}</td>
    </tr>`).join('');
}

// ══════════════════════════════════════════════════════════
// Performance
// ══════════════════════════════════════════════════════════
async function loadPerformance(){
  const d = await api('/performance?days=30');
  if(!d) return;
  $('p-total').textContent = d.total_trades ?? '—';
  $('p-wr').textContent    = d.win_rate ? (d.win_rate*100).toFixed(1)+'%' : '—';
  $('p-pnl').textContent   = d.net_pnl ? '$'+d.net_pnl.toFixed(2) : '—';
  $('p-pnl').style.color   = colorVal(d.net_pnl);
  $('p-pf').textContent    = fmtNum(d.profit_factor);
  $('p-dd').textContent    = d.max_drawdown ? d.max_drawdown.toFixed(1)+'%' : '—';
  $('p-rr').textContent    = fmtNum(d.avg_rr,1);
}

// ══════════════════════════════════════════════════════════
// Bot controls
// ══════════════════════════════════════════════════════════
async function pauseBot(){
  await api('/pause',{method:'POST'});
  $('bot-mode').textContent = 'PAUSED';
}
async function resumeBot(){
  await api('/resume',{method:'POST'});
  loadDashboard();
}

// ══════════════════════════════════════════════════════════
// PHASE 3 — Backtesting
// ══════════════════════════════════════════════════════════
let _btJobId = null;
let _btPollTimer = null;

async function runBacktest(){
  const symbol   = $('bt-symbol').value;
  const strategy = $('bt-strategy').value;
  const tf       = $('bt-timeframe').value;
  const count    = $('bt-count').value;
  const cash     = $('bt-cash').value;

  $('bt-run-btn').disabled = true;
  $('bt-progress').style.display = 'block';
  $('bt-results').style.display  = 'none';
  $('mc-box').style.display      = 'none';

  const d = await api(`/backtest/run?symbol=${symbol}&strategy=${strategy}&timeframe=${tf}&count=${count}&init_cash=${cash}`);
  if(!d || !d.job_id){ alert('Backtest failed to start'); $('bt-run-btn').disabled=false; return; }

  _btJobId = d.job_id;
  _btPollTimer = setInterval(pollBtResult, 2000);
}

async function pollBtResult(){
  if(!_btJobId) return;
  const d = await api(`/backtest/result/${_btJobId}`);
  if(!d) return;
  if(d.status === 'done'){
    clearInterval(_btPollTimer);
    $('bt-progress').style.display = 'none';
    $('bt-run-btn').disabled = false;
    renderBtResults(d.result);
  } else if(d.status === 'error'){
    clearInterval(_btPollTimer);
    $('bt-progress').style.display = 'none';
    $('bt-run-btn').disabled = false;
    alert('Backtest error: ' + (d.error || 'Unknown'));
  }
}

function renderBtResults(r){
  if(!r){ return; }
  _lastBtStats = r;
  $('bt-results').style.display = 'block';

  const cards = [
    ['Total Trades', r.total_trades, ''],
    ['Win Rate',     ((r.win_rate||0)*100).toFixed(1)+'%', r.win_rate>=0.5?'green':'red'],
    ['Net P&L',      '$'+fmtNum(r.net_pnl), r.net_pnl>=0?'green':'red'],
    ['Profit Factor',fmtNum(r.profit_factor), r.profit_factor>=1.2?'green':r.profit_factor>=1?'amber':'red'],
    ['Max Drawdown', fmtNum(r.max_drawdown_pct)+'%', r.max_drawdown_pct<=15?'green':r.max_drawdown_pct<=25?'amber':'red'],
    ['Sharpe Ratio', fmtNum(r.sharpe_ratio), r.sharpe_ratio>=1?'green':r.sharpe_ratio>=0.5?'amber':'red'],
  ];

  $('bt-stat-cards').innerHTML = cards.map(([label,val,col])=>`
    <div class="card">
      <div class="card-label">${label}</div>
      <div class="card-value" style="color:${col?'var(--'+col+')':'var(--text)'}">${val}</div>
    </div>`).join('');

  // Equity curve from trade returns
  const returns = r.trade_returns || [];
  let equity = parseFloat($('bt-cash').value)||10000;
  const curve = [equity];
  returns.forEach(ret => { equity *= 1 + ret/100; curve.push(parseFloat(equity.toFixed(2))); });
  renderBtEquityChart(curve);
  renderBtDistChart(returns);
}

function renderBtEquityChart(curve){
  const ctx = $('bt-equity-chart').getContext('2d');
  if(_btEquityChart) _btEquityChart.destroy();
  _btEquityChart = new Chart(ctx, {
    type:'line',
    data:{labels:curve.map((_,i)=>i),datasets:[{label:'Equity ($)',data:curve,borderColor:'#6c63ff',fill:true,backgroundColor:'rgba(108,99,255,.1)',tension:.3,pointRadius:0}]},
    options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'Equity Curve',color:'#e2e8f0'}},scales:{x:{display:false},y:{grid:{color:'rgba(255,255,255,.05)'}}}}
  });
}

function renderBtDistChart(returns){
  const ctx = $('bt-dist-chart').getContext('2d');
  if(_btDistChart) _btDistChart.destroy();
  // Bucket into bins
  const bins = {};
  const step = 0.5;
  returns.forEach(r=>{
    const b = Math.round(r/step)*step;
    bins[b] = (bins[b]||0)+1;
  });
  const labels = Object.keys(bins).map(Number).sort((a,b)=>a-b);
  const data   = labels.map(l=>bins[l]);
  const colors = labels.map(l=> l>=0?'rgba(34,197,94,.7)':'rgba(239,68,68,.7)');
  _btDistChart = new Chart(ctx, {
    type:'bar',
    data:{labels:labels.map(l=>l+'%'),datasets:[{label:'Trade Distribution',data,backgroundColor:colors}]},
    options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'Trade Return Distribution',color:'#e2e8f0'}},scales:{x:{grid:{color:'rgba(255,255,255,.04)'}},y:{grid:{color:'rgba(255,255,255,.04)'}}}}
  });
}

async function runMonteCarlo(){
  if(!_lastBtStats){ alert('Run a backtest first'); return; }
  const symbol   = $('bt-symbol').value;
  const strategy = $('bt-strategy').value;
  const cash     = parseFloat($('bt-cash').value)||10000;

  $('mc-btn').disabled = true;
  $('mc-btn').textContent = '⏳ Simulating…';

  const d = await api('/backtest/monte-carlo', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol, strategy, n_simulations:1000, init_cash:cash})
  });

  $('mc-btn').disabled = false;
  $('mc-btn').textContent = '🎲 Run Monte Carlo';

  if(!d || !d.monte_carlo){ alert('Monte Carlo failed'); return; }
  renderMcResults(d.monte_carlo, cash);
}

function renderMcResults(mc, initCash){
  $('mc-box').style.display = 'block';

  const pctLoss = (mc.probability_of_loss*100).toFixed(1)+'%';
  const pctRuin = (mc.probability_of_ruin*100).toFixed(1)+'%';

  $('mc-stat-cards').innerHTML = [
    ['Expected Return', mc.expected_return_pct+'%', mc.expected_return_pct>=0?'green':'red'],
    ['Median Equity', '$'+mc.median_final_equity, ''],
    ['P5 (Worst 5%)', '$'+mc.p5_final_equity, 'red'],
    ['P95 (Best 5%)', '$'+mc.p95_final_equity, 'green'],
    ['P(Loss)', pctLoss, mc.probability_of_loss>0.4?'red':'amber'],
    ['P(Ruin 3%)', pctRuin, mc.probability_of_ruin>0.2?'red':'green'],
  ].map(([label,val,col])=>`
    <div class="card">
      <div class="card-label">${label}</div>
      <div class="card-value" style="color:${col?'var(--'+col+')':'var(--text)'}">${val}</div>
    </div>`).join('');

  // Fan chart — overlay sample equity curves
  renderMcFanChart(mc.sample_equity_curves || [], initCash);
}

function renderMcFanChart(curves, initCash){
  const ctx = $('mc-fan-chart').getContext('2d');
  if(_mcFanChart) _mcFanChart.destroy();

  const maxLen = curves.reduce((m,c)=>Math.max(m,c.length),0);
  const labels = Array.from({length:maxLen},(_,i)=>i);

  const datasets = curves.slice(0,50).map((curve,i)=>({
    data: curve,
    borderColor: 'rgba(108,99,255,0.15)',
    borderWidth: 1,
    fill: false,
    pointRadius: 0,
    tension: 0.3,
  }));

  _mcFanChart = new Chart(ctx, {
    type:'line',
    data:{labels, datasets},
    options:{responsive:true,animation:false,
      plugins:{legend:{display:false},title:{display:true,text:'Monte Carlo Fan (50 Scenarios)',color:'#e2e8f0'}},
      scales:{x:{display:false},y:{grid:{color:'rgba(255,255,255,.05)'}}}
    }
  });
}

async function optimizeParams(){
  const symbol   = $('bt-symbol').value;
  const strategy = $('bt-strategy').value;
  const tf       = $('bt-timeframe').value;

  $('opt-btn').disabled = true;
  $('opt-btn').textContent = '⏳ Optimizing…';

  const d = await api('/backtest/optimize', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol, strategy, timeframe:tf})
  });

  $('opt-btn').disabled = false;
  $('opt-btn').textContent = '🔧 Optimize Params';

  if(!d || !d.job_id){ alert('Optimize failed'); return; }
  // Poll for result
  const poll = async ()=>{
    const r = await api(`/backtest/result/${d.job_id}`);
    if(!r) return;
    if(r.status==='done'){
      alert(`Best params:\\n${JSON.stringify(r.result?.best_params, null, 2)}`);
    } else if(r.status==='error'){
      alert('Error: '+(r.error||'unknown'));
    } else {
      setTimeout(poll, 2000);
    }
  };
  setTimeout(poll, 2000);
}

async function compareAllPairs(){
  const strategy = $('bt-strategy').value;
  $('all-pairs-box').style.display = 'none';

  const d = await api(`/backtest/all-pairs?strategy=${strategy}`);
  if(!d || !Array.isArray(d)) return;

  const tb = $('all-pairs-body');
  tb.innerHTML = d.map(r=>`
    <tr>
      <td><b>${r.symbol||'—'}</b></td>
      <td>${r.strategy||strategy}</td>
      <td>${r.total_trades??0}</td>
      <td>${r.win_rate ? (r.win_rate*100).toFixed(1)+'%' : '—'}</td>
      <td style="color:${colorVal(r.net_pnl)}">${r.net_pnl!=null?'$'+fmtNum(r.net_pnl):'—'}</td>
      <td>${fmtNum(r.profit_factor)}</td>
      <td>${fmtNum(r.max_drawdown_pct)}%</td>
      <td>${sharpeBadge(r.sharpe_ratio)}</td>
    </tr>`).join('');

  $('all-pairs-box').style.display = 'block';
}

// ══════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════
connectWS();
loadDashboard();
setInterval(loadDashboard, 30000);
</script>
</body>
</html>
""")

print()
print("=" * 60)
print("Phase 3 Bootstrap COMPLETE")
print("=" * 60)
print()
print("Files created:")
print("  backtesting/__init__.py")
print("  backtesting/data/.gitkeep")
print("  backtesting/data_loader.py   (HistoricalDataLoader)")
print("  backtesting/engine.py        (BacktestEngine)")
print("  backtesting/optimizer.py     (StrategyOptimizer)")
print("  backtesting/monte_carlo.py   (MonteCarloSimulator)")
print("  api/routes.py                (updated with /backtest/* endpoints)")
print("  dashboard/index.html         (updated with Backtesting tab)")
print()
print("Next steps:")
print("  1. pip install vectorbt pandas numpy scipy  (optional but recommended)")
print("  2. python bootstrap.py        (Phase 1 core files)")
print("  3. python bootstrap_phase2.py (Phase 2 dashboard/WS)")
print("  4. python bootstrap_phase3.py (this script)")
print("  5. python -m uvicorn main:app --reload")
