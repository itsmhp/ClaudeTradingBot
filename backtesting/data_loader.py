"""
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
        # Ensure symbol is visible in Market Watch before requesting rates
        if not mt5.symbol_select(symbol, True):
            err = mt5.last_error()
            raise DataLoadError(
                f"Cannot select symbol {symbol} in MT5 Market Watch. Error: {err}"
            )
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
