"""
core/risk_manager.py
====================
Position sizing and signal validation for ClaudeTradingBot.

Implements:
- Lot-size formula from MASTER_CONTEXT section 10
- Signal validation (R:R, spread cap, confidence)
- Daily loss monitoring
- Position-limit checks
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from core.mt5_bridge import MT5Bridge
    from core.signal_engine import TradeSignal


# Fallback instrument specs (same as mt5_bridge)
_INSTRUMENT_SPECS: dict[str, dict] = {
    "XAUUSD":  {"point": 0.01,    "contract_size": 100,    "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "BTCUSD":  {"point": 0.01,    "contract_size": 1,      "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "EURUSD":  {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "GBPUSD":  {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "USDJPY":  {"point": 0.001,   "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "NAS100":  {"point": 0.01,    "contract_size": 1,      "volume_min": 0.1,  "volume_max": 100, "volume_step": 0.1},
    "US30":    {"point": 0.01,    "contract_size": 1,      "volume_min": 0.1,  "volume_max": 100, "volume_step": 0.1},
}


class RiskManager:
    """Enforces all risk rules for ClaudeTradingBot."""

    def __init__(self) -> None:
        self.risk_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
        self.max_loss_usd: float = float(os.getenv("MAX_LOSS_PER_TRADE_USD", "0"))  # 0 = use risk_pct
        self.default_min_rr: float = float(os.getenv("DEFAULT_RR_RATIO", "2.0"))
        self.max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
        self.max_total_positions: int = int(os.getenv("MAX_TOTAL_POSITIONS", "5"))
        self.max_positions_per_pair: int = int(os.getenv("MAX_POSITIONS_PER_PAIR", "2"))
        self._load_spread_caps()

    def _load_spread_caps(self) -> None:
        """Load spread caps from strategies/rules.json."""
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        try:
            with open(rules_path, encoding="utf-8") as f:
                rules = json.load(f)
            self._spread_caps: dict[str, int] = {
                symbol: int(cfg.get("spread_cap_points", 9999))
                for symbol, cfg in rules.get("instrument_config", {}).items()
            }
        except FileNotFoundError:
            logger.warning("strategies/rules.json not found; spread caps unavailable")
            self._spread_caps = {}

    # ── Position Sizing ─────────────────────────────────────────

    def calculate_lot_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        account_equity: float,
        symbol_info: dict | None = None,
    ) -> float:
        """Calculate the appropriate lot size for a trade.

        Formula (from MASTER_CONTEXT section 10):
            lot = (equity × risk_pct/100) / (sl_distance_pts × point_value_per_lot)

        where:
            sl_distance_pts   = |entry - sl| / point
            point_value_per_lot = contract_size × point

        This simplifies to:
            lot = (equity × risk_pct/100) / (|entry - sl| × contract_size)

        Parameters
        ----------
        symbol_info : optional override (used in tests); falls back to
                      built-in instrument specs table.
        """
        specs = symbol_info or _INSTRUMENT_SPECS.get(symbol, _INSTRUMENT_SPECS["EURUSD"])
        point: float = specs["point"]
        contract_size: float = specs["contract_size"]
        volume_min: float = specs["volume_min"]
        volume_max: float = specs["volume_max"]
        volume_step: float = specs["volume_step"]

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            logger.warning(f"calculate_lot_size: sl_distance=0 for {symbol}")
            return volume_min

        # Fixed dollar risk takes priority over percentage risk
        if self.max_loss_usd > 0:
            risk_amount = self.max_loss_usd
        else:
            risk_amount = account_equity * (self.risk_pct / 100.0)
        sl_distance_points = sl_distance / point
        point_value_per_lot = contract_size * point

        raw_lot = risk_amount / (sl_distance_points * point_value_per_lot)

        # Round DOWN to nearest volume_step
        steps = math.floor(raw_lot / volume_step)
        lot = round(steps * volume_step, 8)

        # Clamp to broker limits
        lot = max(volume_min, min(volume_max, lot))
        return round(lot, 2)

    # ── Signal Validation ────────────────────────────────────────

    def validate_signal(
        self,
        signal: "TradeSignal",
        current_spread: int = 0,
    ) -> tuple[bool, str]:
        """Validate a TradeSignal against trading rules.

        Checks (in order):
        1. R:R ratio >= default_min_rr
        2. Spread <= instrument cap
        3. Confidence >= 60 (redundant with Pydantic, kept for defence)

        Returns
        -------
        (True, "")             — signal is acceptable
        (False, reason_string) — signal rejected with reason
        """
        rr = signal.risk_reward_ratio
        if rr < self.default_min_rr:
            return False, f"R:R ratio {rr:.2f} below minimum {self.default_min_rr}"

        if current_spread > 0:
            cap = self._spread_caps.get(signal.pair, 9999)
            if current_spread > cap:
                return False, (
                    f"Spread {current_spread} points exceeds cap {cap} for {signal.pair}"
                )

        if signal.confidence < 60:
            return False, f"Confidence {signal.confidence}% below 60% threshold"

        return True, ""

    # ── Daily Loss ───────────────────────────────────────────────

    async def check_daily_loss(
        self, mt5_bridge: "MT5Bridge"
    ) -> tuple[bool, float]:
        """Check whether the daily loss limit has been breached.

        Returns
        -------
        (is_limit_breached: bool, current_loss_pct: float)
        """
        deals = await mt5_bridge.get_daily_deals()
        total_profit = sum(getattr(d, "profit", 0) for d in deals)
        account_info = await mt5_bridge.get_account_info()
        equity = account_info.get("equity", 10000.0)

        current_loss = -total_profit if total_profit < 0 else 0.0
        current_loss_pct = (current_loss / equity * 100) if equity > 0 else 0.0

        breached = current_loss_pct >= self.max_daily_loss_pct
        if breached:
            logger.critical(f"Daily loss limit breached: {current_loss_pct:.2f}%")
        return breached, round(current_loss_pct, 4)

    # ── Position Limits ──────────────────────────────────────────

    async def check_position_limits(
        self, symbol: str, mt5_bridge: "MT5Bridge"
    ) -> tuple[bool, str]:
        """Check whether position limits allow a new trade.

        Returns
        -------
        (can_trade: bool, reason: str)
        """
        all_positions = await mt5_bridge.get_open_positions()
        if len(all_positions) >= self.max_total_positions:
            return False, (
                f"Max total positions reached ({self.max_total_positions})"
            )

        symbol_positions = await mt5_bridge.get_open_positions(symbol)
        if len(symbol_positions) >= self.max_positions_per_pair:
            return False, (
                f"Max positions per pair reached for {symbol} "
                f"({self.max_positions_per_pair})"
            )

        return True, ""
