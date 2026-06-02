"""
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
