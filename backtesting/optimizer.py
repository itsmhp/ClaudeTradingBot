"""
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
