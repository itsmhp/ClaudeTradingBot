"""
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
