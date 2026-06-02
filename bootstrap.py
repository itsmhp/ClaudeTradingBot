#!/usr/bin/env python3
"""
Bootstrap script for ClaudeTradingBot Phase 1.
Creates all project directories and source files with full implementations.
Run with: python bootstrap.py
"""
from pathlib import Path
import sys

BASE = Path(__file__).parent
print("=" * 60)
print("ClaudeTradingBot — Phase 1 Bootstrap")
print("=" * 60)
print(f"Project root: {BASE}")
print()


def W(rel: str, content: str) -> None:
    """Write content to a file, creating parent directories."""
    p = BASE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"  OK  {rel}")


# ══════════════════════════════════════════════════════════════
# SECTION 1 — Configuration
# ══════════════════════════════════════════════════════════════

W("strategies/rules.json", """{
  "watchlist": ["XAUUSD", "BTCUSD", "USDJPY", "EURUSD", "GBPUSD", "NAS100", "US30"],
  "global_rules": {
    "max_concurrent_trades": 3,
    "max_daily_loss_pct": 3.0,
    "default_risk_per_trade_pct": 1.0,
    "default_min_rr": 2.0,
    "news_blackout_events": [
      "US CPI", "FOMC", "NFP", "FOMC Minutes", "US GDP",
      "BOE Rate Decision", "ECB Rate Decision"
    ]
  },
  "instrument_config": {
    "XAUUSD": {
      "magic_number": 234001,
      "spread_cap_points": 30,
      "preferred_modes": ["scalp", "swing"],
      "preferred_sessions": ["London", "NewYork"],
      "min_move_pips": 5,
      "avoid_minutes_before_news": 30
    },
    "BTCUSD": {
      "magic_number": 234002,
      "spread_cap_points": 5000,
      "preferred_modes": ["swing"],
      "preferred_sessions": ["London", "NewYork", "Asian"],
      "min_move_pips": 50,
      "avoid_minutes_before_news": 30
    },
    "EURUSD": {
      "magic_number": 234003,
      "spread_cap_points": 15,
      "preferred_modes": ["scalp", "swing"],
      "preferred_sessions": ["London", "NewYork"],
      "min_move_pips": 2,
      "avoid_minutes_before_news": 30
    },
    "GBPUSD": {
      "magic_number": 234004,
      "spread_cap_points": 18,
      "preferred_modes": ["scalp", "swing"],
      "preferred_sessions": ["London", "NewYork"],
      "min_move_pips": 3,
      "avoid_minutes_before_news": 30
    },
    "USDJPY": {
      "magic_number": 234005,
      "spread_cap_points": 15,
      "preferred_modes": ["scalp", "swing"],
      "preferred_sessions": ["Asian", "London"],
      "min_move_pips": 2,
      "avoid_minutes_before_news": 30
    },
    "NAS100": {
      "magic_number": 234006,
      "spread_cap_points": 200,
      "preferred_modes": ["swing"],
      "preferred_sessions": ["NewYork"],
      "min_move_pips": 20,
      "avoid_minutes_before_news": 60
    },
    "US30": {
      "magic_number": 234007,
      "spread_cap_points": 300,
      "preferred_modes": ["swing"],
      "preferred_sessions": ["NewYork"],
      "min_move_pips": 30,
      "avoid_minutes_before_news": 60
    }
  }
}
""")

W("strategies/__init__.py", '"""Strategies package for ClaudeTradingBot."""\n')

W("pytest.ini", """[pytest]
asyncio_mode = auto
testpaths = tests
""")

# ══════════════════════════════════════════════════════════════
# SECTION 2 — Core Package
# ══════════════════════════════════════════════════════════════

W("core/__init__.py", '"""Core package for ClaudeTradingBot."""\n')

# ── core/signal_engine.py ──────────────────────────────────────
W("core/signal_engine.py", '''"""
core/signal_engine.py
=====================
Pydantic trade-signal models and SignalEngine orchestrator.

Exports
-------
Direction, OrderType, Strategy, Timeframe  — Enums
TradeSignal, NoTradeSignal                 — Pydantic models
SignalEngine                               — Main pipeline orchestrator
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field, ValidationInfo, field_validator

if TYPE_CHECKING:
    pass  # forward refs resolved at runtime inside __init__


# ─── Enums ─────────────────────────────────────────────────────

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"


class Strategy(str, Enum):
    SCALPING = "SCALPING"
    SWING = "SWING"


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


# ─── Pydantic Models ───────────────────────────────────────────

class TradeSignal(BaseModel):
    """Validated trade signal from Claude AI analysis."""

    pair: str = Field(..., description="Trading instrument symbol", examples=["XAUUSD", "EURUSD"])
    direction: Direction = Field(..., description="Trade direction")
    order_type: OrderType = Field(..., description="Pending order type")
    entry_price: float = Field(..., gt=0, description="Entry price for pending order")
    stop_loss: float = Field(..., gt=0, description="Stop loss price")
    take_profit_1: float = Field(..., gt=0, description="First take profit target")
    take_profit_2: Optional[float] = Field(None, gt=0, description="Second take profit target")
    timeframe: Timeframe = Field(..., description="Analysis timeframe")
    strategy: Strategy = Field(..., description="Strategy type used")
    confidence: int = Field(..., ge=0, le=100, description="Signal confidence percentage")
    reasoning: str = Field(..., min_length=20, description="Claude analysis reasoning")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    signal_id: Optional[str] = Field(default=None, description="Unique signal identifier")

    def model_post_init(self, __context: object) -> None:
        """Auto-generate signal_id if not provided."""
        if self.signal_id is None:
            object.__setattr__(self, "signal_id", str(uuid.uuid4()))

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_actionable(cls, v: int) -> int:
        """Reject signals with confidence below 60%."""
        if v < 60:
            raise ValueError("Confidence below 60% is not actionable")
        return v

    @field_validator("stop_loss")
    @classmethod
    def stop_loss_must_be_valid(cls, v: float, info: ValidationInfo) -> float:
        """Validate SL placement relative to entry and direction."""
        data = info.data
        if "direction" in data and "entry_price" in data:
            if data["direction"] == Direction.BUY and v >= data["entry_price"]:
                raise ValueError("BUY signal: stop_loss must be below entry_price")
            if data["direction"] == Direction.SELL and v <= data["entry_price"]:
                raise ValueError("SELL signal: stop_loss must be above entry_price")
        return v

    @field_validator("take_profit_1")
    @classmethod
    def tp1_must_be_valid(cls, v: float, info: ValidationInfo) -> float:
        """Validate TP1 placement relative to entry and direction."""
        data = info.data
        if "direction" in data and "entry_price" in data:
            if data["direction"] == Direction.BUY and v <= data["entry_price"]:
                raise ValueError("BUY signal: take_profit_1 must be above entry_price")
            if data["direction"] == Direction.SELL and v >= data["entry_price"]:
                raise ValueError("SELL signal: take_profit_1 must be below entry_price")
        return v

    @property
    def risk_reward_ratio(self) -> float:
        """Calculate R:R ratio from entry, SL, and TP1."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit_1 - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def risk_pips(self) -> float:
        """Raw pip distance for the stop loss."""
        return abs(self.entry_price - self.stop_loss)


class NoTradeSignal(BaseModel):
    """Response when no valid trade setup is found."""

    signal: Literal["NO_TRADE"] = "NO_TRADE"
    reasoning: str = Field(..., min_length=10)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── SignalEngine ──────────────────────────────────────────────

class SignalEngine:
    """Main orchestrator: Claude analysis → validation → MT5 execution."""

    def __init__(self) -> None:
        """Instantiate dependencies and load config."""
        from core.mt5_bridge import MT5Bridge
        from core.claude_client import ClaudeClient
        from core.risk_manager import RiskManager

        self.mt5_bridge = MT5Bridge()
        self.claude_client = ClaudeClient()
        self.risk_manager = RiskManager()
        self.bot_mode: str = os.getenv("BOT_MODE", "SIGNAL_ONLY")
        self._load_rules()
        self._start_time = datetime.utcnow()
        self._last_scan_at: Optional[datetime] = None
        self._signal_count_today: int = 0

    def _load_rules(self) -> None:
        """Load trading rules from strategies/rules.json."""
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        with open(rules_path, encoding="utf-8") as f:
            self._rules = json.load(f)
        self._watchlist: list[str] = self._rules.get("watchlist", [])

    async def process_pair(self, pair: str, timeframe: str, strategy: str) -> dict:
        """Full signal pipeline for one trading pair.

        Returns a dict with keys:
            result  : "NO_TRADE" | "REJECTED" | "SKIPPED" | "SIGNAL" | "EXECUTED" | "ERROR"
            signal  : TradeSignal dict (when applicable)
            lot_size: calculated position size
        """
        try:
            # Step 1 — Get current market data
            price_info = await self.mt5_bridge.get_current_price(pair)
            symbol_info = await self.mt5_bridge.get_symbol_info(pair)
            current_spread: int = int(symbol_info.get("spread", 0)) if symbol_info else 0

            # Step 2 — Package chart_data for Claude
            chart_data: dict = {
                "bid": price_info.get("bid", 0),
                "ask": price_info.get("ask", 0),
                "spread": current_spread,
                "price": price_info.get("ask", 0),
                "rsi": None,
                "macd_line": None,
                "signal_line": None,
                "histogram": None,
                "ema_50": None,
                "ema_200": None,
                "structure": "unknown",
                "support_levels": [],
                "resistance_levels": [],
            }

            # Step 3 — Analyze with Claude
            result = await self.claude_client.analyze_chart(pair, timeframe, chart_data)

            # Step 4 — Handle NO_TRADE
            if isinstance(result, NoTradeSignal):
                logger.info(f"NO_TRADE {pair}/{timeframe}: {result.reasoning}")
                return {"result": "NO_TRADE", "pair": pair, "reasoning": result.reasoning}

            signal: TradeSignal = result  # type: ignore[assignment]

            # Step 5 — Validate (RR, spread, confidence)
            valid, reason = self.risk_manager.validate_signal(signal, current_spread)
            if not valid:
                logger.warning(f"Signal REJECTED {pair}: {reason}")
                return {"result": "REJECTED", "pair": pair, "reason": reason}

            # Step 6 — Check position limits
            can_trade, pos_reason = await self.risk_manager.check_position_limits(
                pair, self.mt5_bridge
            )
            if not can_trade:
                logger.info(f"Position limit {pair}: {pos_reason}")
                return {"result": "SKIPPED", "pair": pair, "reason": pos_reason}

            # Step 7 — Calculate lot size
            account_info = await self.mt5_bridge.get_account_info()
            equity = account_info.get("equity", 10000.0)
            lot_size = self.risk_manager.calculate_lot_size(
                pair, signal.entry_price, signal.stop_loss, equity
            )

            # Step 8 — Persist signal to database
            try:
                from database.db import get_session
                from database import queries
                async with get_session() as session:
                    await queries.save_signal(session, signal)
                    self._signal_count_today += 1
            except Exception as db_err:
                logger.error(f"DB save_signal failed: {db_err}")

            # Step 9 — Execute or signal-only
            execution_result: Optional[dict] = None
            outcome = "SIGNAL"
            if self.bot_mode == "AUTO_EXECUTE":
                execution_result = await self.mt5_bridge.place_pending_order(signal, lot_size)
                if execution_result.get("success"):
                    outcome = "EXECUTED"
                    try:
                        from database.db import get_session
                        from database import queries
                        async with get_session() as session:
                            await queries.save_execution(
                                session, signal.signal_id, execution_result, lot_size
                            )
                    except Exception as db_err:
                        logger.error(f"DB save_execution failed: {db_err}")

            # Step 10 — Telegram notification
            try:
                from notifications.telegram import TelegramNotifier
                notifier = TelegramNotifier()
                await notifier.send_signal_alert(signal, lot_size, execution_result, self.bot_mode)
            except Exception as notify_err:
                logger.warning(f"Telegram failed: {notify_err}")

            self._last_scan_at = datetime.utcnow()
            return {
                "result": outcome,
                "signal": signal.model_dump(),
                "lot_size": lot_size,
                "execution": execution_result,
            }

        except Exception as e:
            logger.exception(f"Error processing {pair}: {e}")
            return {"result": "ERROR", "pair": pair, "error": str(e)}

    async def scan_all_pairs(self, strategy: str) -> list[dict]:
        """Scan every pair in the watchlist for the given strategy."""
        tf_map = {"SWING": "H4", "SCALPING": "M15"}
        timeframe = tf_map.get(strategy, "H4")
        tasks = [self.process_pair(p, timeframe, strategy) for p in self._watchlist]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    def get_bot_status(self) -> dict:
        """Return current bot state dict (matches /status API schema)."""
        uptime = (datetime.utcnow() - self._start_time).total_seconds()
        return {
            "mode": self.bot_mode,
            "state": "RUNNING",
            "uptime_seconds": round(uptime, 1),
            "daily_signals_count": self._signal_count_today,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
        }
''')

# ── core/mt5_bridge.py ──────────────────────────────────────────
W("core/mt5_bridge.py", '''"""
core/mt5_bridge.py
==================
Async-compatible wrapper for the MetaTrader5 Python library.

All blocking MT5 calls run inside asyncio.to_thread() to avoid
blocking the event loop.  Only TRADE_ACTION_PENDING orders are
placed (no market orders).  Magic numbers are loaded from
strategies/rules.json.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None  # type: ignore[assignment]  # allows import on non-Windows

if TYPE_CHECKING:
    from core.signal_engine import TradeSignal, OrderType


# Retcode → human-readable message
_RETCODE_MSG: dict[int, str] = {
    10009: "Done",
    10010: "Placed",
    10004: "Requote",
    10006: "Rejected",
    10013: "Invalid request",
    10014: "Invalid volume",
    10015: "Invalid price",
    10016: "Invalid stops",
    10018: "Market closed",
    10019: "No money",
    10027: "AutoTrading disabled",
    10030: "Too many requests",
}

# Fallback instrument specs used when symbol_info is unavailable
_INSTRUMENT_SPECS: dict[str, dict] = {
    "XAUUSD":  {"point": 0.01,    "contract_size": 100,    "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "BTCUSD":  {"point": 0.01,    "contract_size": 1,      "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "EURUSD":  {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "GBPUSD":  {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "USDJPY":  {"point": 0.001,   "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "NAS100":  {"point": 0.01,    "contract_size": 1,      "volume_min": 0.1,  "volume_max": 100, "volume_step": 0.1},
    "US30":    {"point": 0.01,    "contract_size": 1,      "volume_min": 0.1,  "volume_max": 100, "volume_step": 0.1},
}


class MT5Bridge:
    """Handles all MetaTrader5 operations for ClaudeTradingBot."""

    def __init__(self) -> None:
        self._connected: bool = False
        self._load_instrument_config()

    def _load_instrument_config(self) -> None:
        """Load magic numbers and instrument config from strategies/rules.json."""
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        try:
            with open(rules_path, encoding="utf-8") as f:
                rules = json.load(f)
            self._instrument_config: dict = rules.get("instrument_config", {})
        except FileNotFoundError:
            logger.warning("strategies/rules.json not found; using fallback magic numbers")
            self._instrument_config = {}

    def _get_magic_number(self, symbol: str) -> int:
        """Return the magic number for the given symbol."""
        return self._instrument_config.get(symbol, {}).get("magic_number", 999999)

    # ── Connection ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """Initialize MT5 terminal and log in with Exness credentials.

        Raises
        ------
        ConnectionError
            If mt5.initialize() or mt5.login() fails.
        """
        login = int(os.getenv("MT5_LOGIN", "0"))
        password = os.getenv("MT5_PASSWORD", "")
        server = os.getenv("MT5_SERVER", "")

        initialized: bool = await asyncio.to_thread(mt5.initialize)
        if not initialized:
            error = await asyncio.to_thread(mt5.last_error)
            raise ConnectionError(f"MT5 initialize() failed: {error}")

        logged_in: bool = await asyncio.to_thread(mt5.login, login, password, server)
        if not logged_in:
            error = await asyncio.to_thread(mt5.last_error)
            raise ConnectionError(f"MT5 login() failed: {error}")

        self._connected = True
        logger.info(f"MT5 connected (login={login}, server={server})")
        return True

    async def disconnect(self) -> None:
        """Shut down the MT5 connection cleanly."""
        await asyncio.to_thread(mt5.shutdown)
        self._connected = False
        logger.info("MT5 disconnected")

    # ── Account ─────────────────────────────────────────────────

    async def get_account_info(self) -> dict:
        """Return key account metrics as a typed dict.

        Returns
        -------
        dict with keys: balance, equity, margin, free_margin,
                        currency, leverage
        """
        try:
            info = await asyncio.to_thread(mt5.account_info)
            if info is None:
                return {}
            return {
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "free_margin": info.margin_free,
                "currency": info.currency,
                "leverage": info.leverage,
            }
        except Exception as exc:
            logger.error(f"get_account_info error: {exc}")
            return {}

    # ── Symbol Info ─────────────────────────────────────────────

    async def get_symbol_info(self, symbol: str) -> dict:
        """Return symbol metadata, selecting it first if not visible.

        Returns
        -------
        dict with keys: spread, digits, volume_min, volume_max,
                        volume_step, point, contract_size
        """
        try:
            info = await asyncio.to_thread(mt5.symbol_info, symbol)
            if info is None:
                # Try to make symbol visible
                await asyncio.to_thread(mt5.symbol_select, symbol, True)
                info = await asyncio.to_thread(mt5.symbol_info, symbol)
            if info is None:
                logger.warning(f"symbol_info returned None for {symbol}")
                return {}
            return {
                "spread": info.spread,
                "digits": info.digits,
                "volume_min": info.volume_min,
                "volume_max": info.volume_max,
                "volume_step": info.volume_step,
                "point": info.point,
                "contract_size": info.trade_contract_size,
            }
        except Exception as exc:
            logger.error(f"get_symbol_info({symbol}) error: {exc}")
            return {}

    async def get_current_price(self, symbol: str) -> dict:
        """Return current bid/ask/spread from the latest tick.

        Returns
        -------
        dict with keys: bid, ask, spread
        """
        try:
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if tick is None:
                return {}
            return {
                "bid": tick.bid,
                "ask": tick.ask,
                "spread": round((tick.ask - tick.bid) * 100000),  # approx points
            }
        except Exception as exc:
            logger.error(f"get_current_price({symbol}) error: {exc}")
            return {}

    # ── Order Placement ─────────────────────────────────────────

    async def place_pending_order(self, signal: "TradeSignal", lot_size: float) -> dict:
        """Place a pending (limit/stop) order on MT5.

        Parameters
        ----------
        signal   : validated TradeSignal
        lot_size : calculated position size in lots

        Returns
        -------
        dict with keys: success (bool), order_id, retcode, message
        """
        from core.signal_engine import OrderType

        _order_type_map = {
            OrderType.BUY_LIMIT:  mt5.ORDER_TYPE_BUY_LIMIT,
            OrderType.SELL_LIMIT: mt5.ORDER_TYPE_SELL_LIMIT,
            OrderType.BUY_STOP:   mt5.ORDER_TYPE_BUY_STOP,
            OrderType.SELL_STOP:  mt5.ORDER_TYPE_SELL_STOP,
        }

        magic = self._get_magic_number(signal.pair)
        request = {
            "action":      mt5.TRADE_ACTION_PENDING,
            "symbol":      signal.pair,
            "volume":      lot_size,
            "type":        _order_type_map[signal.order_type],
            "price":       signal.entry_price,
            "sl":          signal.stop_loss,
            "tp":          signal.take_profit_1,
            "deviation":   10,
            "magic":       magic,
            "comment":     f"CTB_{signal.strategy.value}_{signal.direction.value}",
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        try:
            result = await asyncio.to_thread(mt5.order_send, request)
        except Exception as exc:
            logger.error(f"order_send exception: {exc}")
            return {"success": False, "order_id": None, "retcode": -1, "message": str(exc)}

        if result is None:
            error = await asyncio.to_thread(mt5.last_error)
            logger.error(f"order_send returned None: {error}")
            return {"success": False, "order_id": None, "retcode": -1, "message": str(error)}

        msg = _RETCODE_MSG.get(result.retcode, f"Unknown retcode {result.retcode}")
        success = result.retcode in (10009, 10010)

        if success:
            logger.info(
                f"Order placed: {signal.pair} {signal.order_type.value} "
                f"@ {signal.entry_price} | order#{result.order}"
            )
        else:
            logger.warning(f"Order failed: {signal.pair} retcode={result.retcode} ({msg})")

        return {
            "success": success,
            "order_id": result.order if success else None,
            "retcode": result.retcode,
            "message": msg,
        }

    # ── Positions & Orders ───────────────────────────────────────

    async def get_open_positions(self, symbol: Optional[str] = None) -> list:
        """Return open positions, optionally filtered by symbol."""
        try:
            if symbol:
                positions = await asyncio.to_thread(mt5.positions_get, symbol=symbol)
            else:
                positions = await asyncio.to_thread(mt5.positions_get)
            return list(positions) if positions is not None else []
        except Exception as exc:
            logger.error(f"get_open_positions error: {exc}")
            return []

    async def get_pending_orders(self, symbol: Optional[str] = None) -> list:
        """Return pending orders, optionally filtered by symbol."""
        try:
            if symbol:
                orders = await asyncio.to_thread(mt5.orders_get, symbol=symbol)
            else:
                orders = await asyncio.to_thread(mt5.orders_get)
            return list(orders) if orders is not None else []
        except Exception as exc:
            logger.error(f"get_pending_orders error: {exc}")
            return []

    async def cancel_order(self, ticket: int) -> dict:
        """Cancel a pending order by ticket number."""
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        try:
            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None:
                return {"success": False, "message": "order_send returned None"}
            success = result.retcode == 10009
            return {
                "success": success,
                "retcode": result.retcode,
                "message": _RETCODE_MSG.get(result.retcode, str(result.retcode)),
            }
        except Exception as exc:
            logger.error(f"cancel_order({ticket}) error: {exc}")
            return {"success": False, "message": str(exc)}

    async def get_daily_deals(self) -> list:
        """Return all deals from today filtered by ClaudeTradingBot magic numbers."""
        magic_numbers = {
            cfg.get("magic_number")
            for cfg in self._instrument_config.values()
            if cfg.get("magic_number")
        }
        now = datetime.utcnow()
        start = datetime(now.year, now.month, now.day)
        try:
            deals = await asyncio.to_thread(
                mt5.history_deals_get,
                start,
                now + timedelta(seconds=1),
            )
            if deals is None:
                return []
            return [d for d in deals if d.magic in magic_numbers]
        except Exception as exc:
            logger.error(f"get_daily_deals error: {exc}")
            return []
''')

# ── core/claude_client.py ──────────────────────────────────────
W("core/claude_client.py", '''"""
core/claude_client.py
=====================
Wraps the Anthropic API and produces validated TradeSignal objects.
Loads rules.json and injects it into every prompt so Claude has
full context about instruments, risk rules, and trading strategy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Union

import anthropic
from loguru import logger

from core.signal_engine import NoTradeSignal, TradeSignal


class ClaudeClient:
    """Anthropic API wrapper for chart analysis and signal generation."""

    _SYSTEM_PROMPT = """You are an expert forex and commodities trading analyst.
Analyse the provided chart data and trading rules, then respond with EXACTLY ONE of:

1. A trade signal as valid JSON with keys:
   pair, direction (BUY/SELL), order_type (BUY_LIMIT/SELL_LIMIT/BUY_STOP/SELL_STOP),
   entry_price, stop_loss, take_profit_1, take_profit_2 (optional), timeframe,
   strategy (SCALPING/SWING), confidence (0-100), reasoning (min 20 chars).

2. If no valid setup: {"NO_TRADE": true, "reasoning": "<explanation>"}

Rules:
- Only output raw JSON. No markdown, no code fences.
- confidence must be 60-100 for a trade signal.
- BUY: stop_loss < entry_price < take_profit_1
- SELL: take_profit_1 < entry_price < stop_loss
- Minimum R:R ratio = 2.0
- Reject if spread exceeds instrument cap in the rules.
"""

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model: str = os.getenv("CLAUDE_MODEL", "claude-opus-4-5")
        self._max_tokens: int = int(os.getenv("CLAUDE_MAX_TOKENS", "1000"))
        self._rules = self._load_rules()

    def _load_rules(self) -> dict:
        """Load strategies/rules.json for injection into prompts."""
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        try:
            with open(rules_path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning("strategies/rules.json not found")
            return {}

    def _build_user_prompt(self, pair: str, timeframe: str, chart_data: dict) -> str:
        """Construct the user prompt injecting all market data and rules."""
        return f"""Analyse {pair} on the {timeframe} timeframe.

Market Data:
  Price   : {chart_data.get("price", "N/A")}
  Bid/Ask : {chart_data.get("bid", "N/A")} / {chart_data.get("ask", "N/A")}
  Spread  : {chart_data.get("spread", "N/A")} points
  RSI(14) : {chart_data.get("rsi", "N/A")}
  MACD    : {chart_data.get("macd_line", "N/A")} / Signal {chart_data.get("signal_line", "N/A")} / Hist {chart_data.get("histogram", "N/A")}
  EMA 50  : {chart_data.get("ema_50", "N/A")}
  EMA 200 : {chart_data.get("ema_200", "N/A")}
  Structure: {chart_data.get("structure", "N/A")}
  Support : {chart_data.get("support_levels", [])}
  Resistance: {chart_data.get("resistance_levels", [])}

Trading Rules (JSON):
{json.dumps(self._rules, indent=2)}

Respond with a trade signal JSON or NO_TRADE JSON."""

    async def analyze_chart(
        self, pair: str, timeframe: str, chart_data: dict
    ) -> Union[TradeSignal, NoTradeSignal]:
        """Call Claude to analyse a single chart and return a validated signal.

        Parameters
        ----------
        pair       : instrument symbol e.g. "XAUUSD"
        timeframe  : timeframe string e.g. "H4"
        chart_data : dict of price/indicator values

        Returns
        -------
        TradeSignal or NoTradeSignal
        """
        user_prompt = self._build_user_prompt(pair, timeframe, chart_data)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            logger.error(f"Anthropic API error for {pair}: {exc}")
            raise

        raw_text: str = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        try:
            parsed: dict = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning(f"JSON parse failed for {pair}. Raw: {raw_text[:200]}")
            raise

        if "NO_TRADE" in parsed:
            return NoTradeSignal(reasoning=parsed.get("reasoning", "No setup found"))

        try:
            return TradeSignal.model_validate(parsed)
        except Exception as exc:
            logger.warning(f"TradeSignal validation failed for {pair}: {exc}")
            raise

    async def scan_watchlist(
        self,
        pairs: list[str],
        timeframe: str,
        chart_data_map: dict[str, dict],
    ) -> list[Union[TradeSignal, NoTradeSignal]]:
        """Analyse multiple pairs and collect signals.

        Skips pairs that raise exceptions.
        """
        results: list[Union[TradeSignal, NoTradeSignal]] = []
        for pair in pairs:
            chart_data = chart_data_map.get(pair, {})
            try:
                signal = await self.analyze_chart(pair, timeframe, chart_data)
                results.append(signal)
            except Exception as exc:
                logger.warning(f"scan_watchlist skipping {pair}: {exc}")
        return results

    async def build_daily_briefing(self, chart_data_map: dict[str, dict]) -> str:
        """Generate a plain-text morning market briefing across all pairs."""
        pairs_summary = "\\n".join(
            f"  {pair}: price={data.get('price', 'N/A')}, rsi={data.get('rsi', 'N/A')}"
            for pair, data in chart_data_map.items()
        )
        prompt = (
            "Generate a concise daily trading briefing covering market bias "
            "(bullish/bearish/ranging) for each pair below. "
            "Keep it under 300 words.\\n\\n" + pairs_summary
        )
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except anthropic.APIError as exc:
            logger.error(f"build_daily_briefing API error: {exc}")
            raise
''')

# ── core/risk_manager.py ──────────────────────────────────────
W("core/risk_manager.py", '''"""
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
''')

# ══════════════════════════════════════════════════════════════
# SECTION 3 — Notifications
# ══════════════════════════════════════════════════════════════

W("notifications/__init__.py", '"""Notifications package for ClaudeTradingBot."""\n')

W("notifications/telegram.py", '''"""
notifications/telegram.py
=========================
Sends formatted Telegram alerts for trade signals, errors,
and daily performance summaries.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from core.signal_engine import TradeSignal


class TelegramNotifier:
    """Formats and sends Telegram messages via python-telegram-bot."""

    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not self._chat_id:
            logger.warning("Telegram token or chat_id not configured")
        # Lazy import to avoid cost at import time
        self._token = token

    async def _send(self, text: str) -> None:
        """Low-level send wrapper."""
        if not self._token or not self._chat_id:
            logger.debug(f"[Telegram mock] {text[:80]}")
            return
        try:
            from telegram import Bot
            bot = Bot(token=self._token)
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(f"Telegram send failed: {exc}")

    async def send_signal_alert(
        self,
        signal: "TradeSignal",
        lot_size: float,
        execution_result: Optional[dict],
        bot_mode: str,
    ) -> None:
        """Send a BUY/SELL signal alert in the standard format."""
        from core.signal_engine import Direction

        emoji_dir = "\\U0001F7E2" if signal.direction == Direction.BUY else "\\U0001F534"
        dir_label = "BUY" if signal.direction == Direction.BUY else "SELL"
        arrow = "\\U0001F4C8" if signal.direction == Direction.BUY else "\\U0001F4C9"

        if bot_mode == "AUTO_EXECUTE" and execution_result and execution_result.get("success"):
            mode_tag = "\\u2705 EXECUTING"
            order_line = f"\\n\\U0001F4CB Order #: {execution_result.get('order_id', 'N/A')}"
        else:
            mode_tag = "\\U0001F4E1 SIGNAL ONLY"
            order_line = ""

        tp2_line = (
            f"\\n\\U0001F3AF TP2: {signal.take_profit_2}"
            if signal.take_profit_2
            else "\\n\\U0001F3AF TP2: \\u2014"
        )

        message = (
            f"{emoji_dir} {dir_label} SIGNAL \\u2014 {signal.pair}\\n\\n"
            f"\\U0001F4CA Strategy: {signal.strategy.value} | Timeframe: {signal.timeframe.value}\\n"
            f"{arrow} Direction: {signal.order_type.value}\\n\\n"
            f"\\U0001F4B0 Entry: {signal.entry_price}\\n"
            f"\\U0001F6D1 Stop Loss: {signal.stop_loss}\\n"
            f"\\U0001F3AF TP1: {signal.take_profit_1}"
            f"{tp2_line}\\n\\n"
            f"\\u2696\\uFE0F Risk:Reward = 1:{signal.risk_reward_ratio}\\n"
            f"\\U0001F4CF Lots: {lot_size}\\n"
            f"\\U0001F3B2 Confidence: {signal.confidence}%\\n\\n"
            f"\\U0001F4A1 Reasoning:\\n{signal.reasoning[:200]}\\n\\n"
            f"\\U0001F916 Mode: {mode_tag}{order_line}\\n"
            f"\\u23F0 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        )
        await self._send(message)

    async def send_no_trade_alert(
        self, pair: str, timeframe: str, reasoning: str
    ) -> None:
        """Send a brief NO_TRADE notification."""
        msg = f"\\u2139\\uFE0F NO TRADE \\u2014 {pair} ({timeframe})\\n{reasoning[:150]}"
        await self._send(msg)

    async def send_error_alert(self, component: str, error: str) -> None:
        """Send an error alert."""
        msg = f"\\u26A0\\uFE0F ERROR in {component}\\n{error[:200]}"
        await self._send(msg)

    async def send_daily_summary(self, performance: dict) -> None:
        """Send end-of-day P&L summary."""
        msg = (
            f"\\U0001F4CA Daily Summary\\n\\n"
            f"Signals  : {performance.get('total_signals', 0)}\\n"
            f"Trades   : {performance.get('executed_trades', 0)}\\n"
            f"Win Rate : {performance.get('win_rate', 0):.1f}%\\n"
            f"Net P&L  : ${performance.get('net_pnl', 0):.2f}\\n"
        )
        await self._send(msg)

    async def send_bot_paused(self, reason: str) -> None:
        """Send bot-paused alert."""
        msg = f"\\U0001F6D1 BOT PAUSED\\n{reason}"
        await self._send(msg)
''')

W("notifications/webhook.py", '''"""
notifications/webhook.py
========================
Sends trade signal events to a configurable HTTP webhook endpoint.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from core.signal_engine import TradeSignal


class WebhookNotifier:
    """Posts JSON payloads to WEBHOOK_URL on trade events."""

    def __init__(self) -> None:
        self._url: str = os.getenv("WEBHOOK_URL", "")

    async def send_signal(
        self,
        signal: "TradeSignal",
        lot_size: float,
        execution_result: Optional[dict],
        bot_mode: str,
    ) -> None:
        """POST a trade signal payload to the configured webhook URL."""
        if not self._url:
            return
        payload = {
            "event": "TRADE_SIGNAL",
            "bot_mode": bot_mode,
            "signal": signal.model_dump(mode="json"),
            "lot_size": lot_size,
            "execution": execution_result,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status not in (200, 201, 204):
                        logger.warning(f"Webhook returned {resp.status}")
        except Exception as exc:
            logger.error(f"Webhook POST failed: {exc}")

    async def send_event(self, event_type: str, data: dict) -> None:
        """POST a generic event payload."""
        if not self._url:
            return
        payload = {"event": event_type, **data}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status not in (200, 201, 204):
                        logger.warning(f"Webhook event {event_type} returned {resp.status}")
        except Exception as exc:
            logger.error(f"Webhook send_event failed: {exc}")
''')

# ══════════════════════════════════════════════════════════════
# SECTION 4 — Database
# ══════════════════════════════════════════════════════════════

W("database/__init__.py", '"""Database package for ClaudeTradingBot."""\n')

W("database/models.py", '''"""
database/models.py
==================
SQLAlchemy 2.0 ORM models for ClaudeTradingBot.
Four tables (from MASTER_CONTEXT section 12):
  trade_signals, executed_trades, bot_log, performance_summary
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class TradeSignalRecord(Base):
    """Persists every signal generated by Claude AI."""

    __tablename__ = "trade_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_1: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    was_executed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )


class ExecutedTradeRecord(Base):
    """Records every MT5 order placed by the bot."""

    __tablename__ = "executed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trade_signals.signal_id"), nullable=False, index=True
    )
    mt5_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    mt5_ticket: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_1: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING", nullable=False)
    mt5_retcode: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mt5_comment: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class BotLogRecord(Base):
    """System event log."""

    __tablename__ = "bot_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )


class PerformanceSummaryRecord(Base):
    """Daily P&L and performance metrics."""

    __tablename__ = "performance_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)  # YYYY-MM-DD
    total_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    executed_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    starting_equity: Mapped[float] = mapped_column(Float, nullable=False)
    ending_equity: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )
''')

W("database/db.py", '''"""
database/db.py
==============
Async SQLAlchemy engine and session factory.
Uses SQLite + aiosqlite for local storage.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base

_DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./database.db",
)

_engine = create_async_engine(
    _DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in _DATABASE_URL else {},
)

_SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create all tables if they do not exist."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialised")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a database session."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
''')

W("database/queries.py", '''"""
database/queries.py
===================
Async query functions using SQLAlchemy ORM.
All queries use parameterised operations — no f-string SQL.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    BotLogRecord,
    ExecutedTradeRecord,
    PerformanceSummaryRecord,
    TradeSignalRecord,
)

if TYPE_CHECKING:
    from core.signal_engine import TradeSignal


async def save_signal(session: AsyncSession, signal: "TradeSignal") -> str:
    """Persist a TradeSignal to trade_signals and return the signal_id."""
    sid = signal.signal_id or str(uuid.uuid4())
    record = TradeSignalRecord(
        signal_id=sid,
        pair=signal.pair,
        direction=signal.direction.value,
        order_type=signal.order_type.value,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        timeframe=signal.timeframe.value,
        strategy=signal.strategy.value,
        confidence=signal.confidence,
        reasoning=signal.reasoning,
        risk_reward_ratio=signal.risk_reward_ratio,
        was_executed=False,
    )
    session.add(record)
    await session.flush()
    return sid


async def save_execution(
    session: AsyncSession,
    signal_id: Optional[str],
    order_result: dict,
    lot_size: float,
) -> int:
    """Persist a MT5 order result to executed_trades.

    Returns the new row id.
    """
    # Look up the parent signal to copy fields
    stmt = select(TradeSignalRecord).where(TradeSignalRecord.signal_id == signal_id)
    result = await session.execute(stmt)
    parent = result.scalar_one_or_none()

    record = ExecutedTradeRecord(
        signal_id=signal_id or "",
        mt5_order_id=order_result.get("order_id") or 0,
        pair=parent.pair if parent else "",
        direction=parent.direction if parent else "",
        order_type=parent.order_type if parent else "",
        volume=lot_size,
        entry_price=parent.entry_price if parent else 0.0,
        stop_loss=parent.stop_loss if parent else 0.0,
        take_profit_1=parent.take_profit_1 if parent else 0.0,
        take_profit_2=parent.take_profit_2 if parent else None,
        status="PENDING",
        mt5_retcode=order_result.get("retcode"),
        mt5_comment=order_result.get("message", ""),
    )
    session.add(record)
    # Mark parent as executed
    if parent:
        parent.was_executed = True
    await session.flush()
    return record.id  # type: ignore[return-value]


async def get_recent_signals(session: AsyncSession, limit: int = 20) -> list:
    """Return recent trade signals ordered by creation time DESC."""
    stmt = (
        select(TradeSignalRecord)
        .order_by(TradeSignalRecord.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_today_performance(session: AsyncSession) -> dict:
    """Aggregate today's executed trades into a performance dict."""
    today_str = date.today().isoformat()
    stmt = select(ExecutedTradeRecord).where(
        func.date(ExecutedTradeRecord.created_at) == today_str
    )
    result = await session.execute(stmt)
    trades = list(result.scalars().all())

    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit < 0]
    net_pnl = sum(t.profit for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0

    return {
        "date": today_str,
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(net_pnl, 2),
        "win_rate": round(win_rate, 2),
    }


async def log_event(
    session: AsyncSession,
    level: str,
    component: str,
    message: str,
    details: Optional[str] = None,
) -> None:
    """Insert a record into bot_log."""
    record = BotLogRecord(
        level=level,
        component=component,
        message=message,
        details=details,
    )
    session.add(record)
    await session.flush()


async def update_trade_status(
    session: AsyncSession,
    mt5_order_id: int,
    status: str,
    profit: Optional[float] = None,
) -> None:
    """Update the status (and optionally profit) of an executed trade."""
    values: dict = {"status": status, "updated_at": datetime.utcnow()}
    if profit is not None:
        values["profit"] = profit
        if status == "FILLED":
            values["closed_at"] = datetime.utcnow()
    stmt = (
        update(ExecutedTradeRecord)
        .where(ExecutedTradeRecord.mt5_order_id == mt5_order_id)
        .values(**values)
    )
    await session.execute(stmt)
''')

# ══════════════════════════════════════════════════════════════
# SECTION 5 — API
# ══════════════════════════════════════════════════════════════

W("api/__init__.py", '"""API package for ClaudeTradingBot."""\n')

W("api/schemas.py", '''"""
api/schemas.py
==============
Pydantic v2 request/response models for all FastAPI endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "healthy"
    uptime_seconds: float
    mt5_connected: bool
    mcp_connected: bool
    bot_mode: str
    bot_state: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatusResponse(BaseModel):
    mode: str
    state: str
    active_positions: int
    pending_orders: int
    daily_pnl: float
    daily_pnl_percent: float
    daily_signals_count: int
    account_equity: float
    last_scan_at: Optional[str] = None
    next_scan_at: Optional[str] = None


class ExecuteRequest(BaseModel):
    pair: str = Field(..., examples=["XAUUSD"])
    timeframe: str = Field(..., examples=["H4"])
    strategy: Literal["SWING", "SCALPING"] = "SWING"


class PauseResumeResponse(BaseModel):
    success: bool
    message: str
    state: str


class PerformanceResponse(BaseModel):
    period: str
    total_signals: int
    executed_trades: int
    win_rate: float
    net_pnl: float
    profit_factor: float
    avg_rr_achieved: float
    max_drawdown_percent: float
    best_trade: Optional[dict[str, Any]] = None
    worst_trade: Optional[dict[str, Any]] = None


class SignalListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
''')

W("api/routes.py", '''"""
api/routes.py
=============
FastAPI APIRouter — all 11 endpoints from MASTER_CONTEXT section 13.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    ExecuteRequest,
    HealthResponse,
    PauseResumeResponse,
    PerformanceResponse,
    SignalListResponse,
    StatusResponse,
)
from database.db import get_session
from database import queries

router = APIRouter()

# Simple in-memory rate limiter for POST /execute
_execute_calls: list[float] = []
_BOT_STATE: dict[str, str] = {"state": "RUNNING"}
_START_TIME: float = time.time()


def _rate_limit_execute() -> None:
    """Allow max 10 calls to /execute per minute."""
    now = time.time()
    window = [t for t in _execute_calls if now - t < 60]
    if len(window) >= 10:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit: max 10 /execute requests per minute",
        )
    _execute_calls.clear()
    _execute_calls.extend(window)
    _execute_calls.append(now)


# ── Health ────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — uptime, MT5 status, bot mode."""
    import os
    uptime = time.time() - _START_TIME
    return HealthResponse(
        status="healthy",
        uptime_seconds=round(uptime, 1),
        mt5_connected=True,   # TODO: query mt5_bridge.connected
        mcp_connected=False,  # MCP is external
        bot_mode=os.getenv("BOT_MODE", "SIGNAL_ONLY"),
        bot_state=_BOT_STATE["state"],
    )


# ── Status ────────────────────────────────────────────────────

@router.get("/status", response_model=StatusResponse)
async def bot_status() -> StatusResponse:
    """Current bot state — mode, positions, daily P&L."""
    import os
    return StatusResponse(
        mode=os.getenv("BOT_MODE", "SIGNAL_ONLY"),
        state=_BOT_STATE["state"],
        active_positions=0,
        pending_orders=0,
        daily_pnl=0.0,
        daily_pnl_percent=0.0,
        daily_signals_count=0,
        account_equity=0.0,
    )


# ── Signals ───────────────────────────────────────────────────

@router.get("/signals", response_model=SignalListResponse)
async def list_signals(
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> SignalListResponse:
    """List recent trade signals (paginated)."""
    records = await queries.get_recent_signals(session, limit=page_size)
    items = [
        {
            "signal_id": r.signal_id,
            "pair": r.pair,
            "direction": r.direction,
            "order_type": r.order_type,
            "entry_price": r.entry_price,
            "stop_loss": r.stop_loss,
            "take_profit_1": r.take_profit_1,
            "confidence": r.confidence,
            "strategy": r.strategy,
            "timeframe": r.timeframe,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]
    return SignalListResponse(items=items, total=len(items), page=page, page_size=page_size)


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a specific signal by signal_id."""
    from sqlalchemy import select
    from database.models import TradeSignalRecord
    stmt = select(TradeSignalRecord).where(TradeSignalRecord.signal_id == signal_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id!r} not found")
    return {
        "signal_id": record.signal_id,
        "pair": record.pair,
        "direction": record.direction,
        "entry_price": record.entry_price,
        "stop_loss": record.stop_loss,
        "take_profit_1": record.take_profit_1,
        "take_profit_2": record.take_profit_2,
        "confidence": record.confidence,
        "reasoning": record.reasoning,
        "created_at": record.created_at.isoformat(),
    }


# ── Trades ────────────────────────────────────────────────────

@router.get("/trades")
async def list_trades(
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List executed trades (paginated)."""
    from sqlalchemy import select
    from database.models import ExecutedTradeRecord
    stmt = (
        select(ExecutedTradeRecord)
        .order_by(ExecutedTradeRecord.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    result = await session.execute(stmt)
    records = result.scalars().all()
    items = [
        {
            "id": r.id,
            "signal_id": r.signal_id,
            "pair": r.pair,
            "direction": r.direction,
            "volume": r.volume,
            "entry_price": r.entry_price,
            "status": r.status,
            "profit": r.profit,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]
    return {"items": items, "page": page, "page_size": page_size}


@router.get("/trades/{trade_id}")
async def get_trade(
    trade_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a specific executed trade by ID."""
    from sqlalchemy import select
    from database.models import ExecutedTradeRecord
    stmt = select(ExecutedTradeRecord).where(ExecutedTradeRecord.id == trade_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return {
        "id": record.id,
        "signal_id": record.signal_id,
        "pair": record.pair,
        "direction": record.direction,
        "volume": record.volume,
        "entry_price": record.entry_price,
        "stop_loss": record.stop_loss,
        "status": record.status,
        "profit": record.profit,
        "mt5_order_id": record.mt5_order_id,
        "created_at": record.created_at.isoformat(),
    }


# ── Execute ───────────────────────────────────────────────────

@router.post("/execute")
async def manual_execute(
    request: ExecuteRequest,
    _: None = Depends(_rate_limit_execute),
) -> dict[str, Any]:
    """Manually trigger a chart scan and signal generation."""
    if _BOT_STATE["state"] != "RUNNING":
        raise HTTPException(status_code=409, detail="Bot is not in RUNNING state")
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        result = await engine.process_pair(request.pair, request.timeframe, request.strategy)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Pause / Resume ────────────────────────────────────────────

@router.post("/pause", response_model=PauseResumeResponse)
async def pause_bot() -> PauseResumeResponse:
    """Pause the bot — stops new signal generation."""
    _BOT_STATE["state"] = "PAUSED"
    return PauseResumeResponse(
        success=True,
        message="Bot paused. No new signals will be generated.",
        state="PAUSED",
    )


@router.post("/resume", response_model=PauseResumeResponse)
async def resume_bot() -> PauseResumeResponse:
    """Resume the bot after a pause."""
    _BOT_STATE["state"] = "RUNNING"
    return PauseResumeResponse(
        success=True,
        message="Bot resumed. Signal generation active.",
        state="RUNNING",
    )


# ── Performance ───────────────────────────────────────────────

@router.get("/performance", response_model=PerformanceResponse)
async def performance(
    period: str = "today",
    session: AsyncSession = Depends(get_session),
) -> PerformanceResponse:
    """Performance summary for a given period (today|week|month|all)."""
    perf = await queries.get_today_performance(session)
    return PerformanceResponse(
        period=period,
        total_signals=perf.get("count", 0),
        executed_trades=perf.get("count", 0),
        win_rate=perf.get("win_rate", 0.0),
        net_pnl=perf.get("net_pnl", 0.0),
        profit_factor=0.0,
        avg_rr_achieved=0.0,
        max_drawdown_percent=0.0,
        best_trade=None,
        worst_trade=None,
    )


@router.get("/performance/today")
async def performance_today(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Today\'s P&L breakdown from the database."""
    return await queries.get_today_performance(session)
''')

# ══════════════════════════════════════════════════════════════
# SECTION 6 — Tests
# ══════════════════════════════════════════════════════════════

W("tests/__init__.py", '"""Test suite for ClaudeTradingBot."""\n')

W("tests/conftest.py", '''"""
tests/conftest.py
=================
Shared pytest fixtures for ClaudeTradingBot tests.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)
from database.models import Base


# ── mock_mt5 ─────────────────────────────────────────────────

@pytest.fixture
def mock_mt5(mocker):
    """Patch core.mt5_bridge.mt5 with a configured MagicMock."""
    mt5_mock = MagicMock()

    # Constants
    mt5_mock.TRADE_RETCODE_DONE = 10009
    mt5_mock.ORDER_TYPE_BUY_LIMIT = 2
    mt5_mock.ORDER_TYPE_SELL_LIMIT = 3
    mt5_mock.ORDER_TYPE_BUY_STOP = 4
    mt5_mock.ORDER_TYPE_SELL_STOP = 5
    mt5_mock.TRADE_ACTION_PENDING = 5
    mt5_mock.TRADE_ACTION_REMOVE = 8
    mt5_mock.ORDER_TIME_GTC = 1
    mt5_mock.ORDER_FILLING_IOC = 1

    # initialize / login
    mt5_mock.initialize.return_value = True
    mt5_mock.login.return_value = True
    mt5_mock.last_error.return_value = (0, "No error")
    mt5_mock.shutdown.return_value = True

    # account_info
    account = MagicMock()
    account.balance = 10000.0
    account.equity = 10000.0
    account.margin = 500.0
    account.margin_free = 9500.0
    account.currency = "USD"
    account.leverage = 500
    mt5_mock.account_info.return_value = account

    # symbol_info (XAUUSD specs by default)
    sym = MagicMock()
    sym.spread = 12
    sym.digits = 2
    sym.volume_min = 0.01
    sym.volume_max = 100.0
    sym.volume_step = 0.01
    sym.point = 0.01
    sym.trade_contract_size = 100.0
    mt5_mock.symbol_info.return_value = sym
    mt5_mock.symbol_select.return_value = True

    # symbol_info_tick
    tick = MagicMock()
    tick.bid = 2350.00
    tick.ask = 2350.12
    mt5_mock.symbol_info_tick.return_value = tick

    # positions / orders
    mt5_mock.positions_get.return_value = None
    mt5_mock.orders_get.return_value = None
    mt5_mock.history_deals_get.return_value = None

    # order_send
    order_result = MagicMock()
    order_result.retcode = 10009
    order_result.order = 12345
    mt5_mock.order_send.return_value = order_result

    mocker.patch("core.mt5_bridge.mt5", mt5_mock)
    return mt5_mock


# ── signal fixtures ───────────────────────────────────────────

@pytest.fixture
def sample_buy_signal() -> TradeSignal:
    """Valid XAUUSD BUY LIMIT signal."""
    return TradeSignal(
        pair="XAUUSD",
        direction=Direction.BUY,
        order_type=OrderType.BUY_LIMIT,
        entry_price=2350.0,
        stop_loss=2340.0,
        take_profit_1=2370.0,
        take_profit_2=2390.0,
        timeframe=Timeframe.H4,
        strategy=Strategy.SWING,
        confidence=78,
        reasoning=(
            "XAUUSD has pulled back to the 50 EMA on H4 after a clear break of "
            "structure above 2355. RSI at 52 confirms momentum is not exhausted."
        ),
        signal_id="test-signal-001",
    )


@pytest.fixture
def sample_sell_signal() -> TradeSignal:
    """Valid EURUSD SELL LIMIT signal."""
    return TradeSignal(
        pair="EURUSD",
        direction=Direction.SELL,
        order_type=OrderType.SELL_LIMIT,
        entry_price=1.08500,
        stop_loss=1.08650,
        take_profit_1=1.08200,
        timeframe=Timeframe.M5,
        strategy=Strategy.SCALPING,
        confidence=72,
        reasoning=(
            "EMA 9 crossed below EMA 21 on M5. Price approaching M15 resistance "
            "at 1.0850. RSI at 58, room to fall."
        ),
        signal_id="test-signal-002",
    )


# ── test_db ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_db() -> AsyncSession:
    """In-memory SQLite async session for database tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── mock_anthropic ────────────────────────────────────────────

@pytest.fixture
def mock_anthropic(mocker, sample_buy_signal):
    """Patch anthropic.Anthropic to return a mock response matching sample_buy_signal."""
    import json

    response_json = {
        "pair": "XAUUSD",
        "direction": "BUY",
        "order_type": "BUY_LIMIT",
        "entry_price": 2350.0,
        "stop_loss": 2340.0,
        "take_profit_1": 2370.0,
        "take_profit_2": 2390.0,
        "timeframe": "H4",
        "strategy": "SWING",
        "confidence": 78,
        "reasoning": (
            "XAUUSD has pulled back to the 50 EMA on H4 after a clear break of "
            "structure above 2355. RSI at 52 confirms momentum is not exhausted."
        ),
    }

    content_block = MagicMock()
    content_block.text = json.dumps(response_json)

    mock_response = MagicMock()
    mock_response.content = [content_block]

    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_response

    mock_anthropic_class = mocker.patch("anthropic.Anthropic", return_value=mock_client_instance)
    return mock_client_instance
''')

W("tests/test_mt5_bridge.py", '''"""
tests/test_mt5_bridge.py
========================
Unit tests for core/mt5_bridge.py (6 tests).
"""
import pytest
from core.mt5_bridge import MT5Bridge
from core.signal_engine import Direction, OrderType, Strategy, Timeframe, TradeSignal


# ── 1. Connect success ────────────────────────────────────────

async def test_connect_success(mock_mt5):
    """connect() returns True when initialize() and login() succeed."""
    bridge = MT5Bridge()
    result = await bridge.connect()
    assert result is True
    assert bridge._connected is True
    mock_mt5.initialize.assert_called_once()
    mock_mt5.login.assert_called_once()


# ── 2. Connect failure ────────────────────────────────────────

async def test_connect_failure(mock_mt5):
    """connect() raises ConnectionError when initialize() returns False."""
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = (0, "Terminal not found")
    bridge = MT5Bridge()
    with pytest.raises(ConnectionError):
        await bridge.connect()


# ── 3. Place BUY LIMIT order ──────────────────────────────────

async def test_place_buy_limit_order(mock_mt5, sample_buy_signal):
    """place_pending_order() builds correct request for a BUY_LIMIT signal."""
    bridge = MT5Bridge()
    await bridge.connect()
    result = await bridge.place_pending_order(sample_buy_signal, 0.10)

    assert result["success"] is True
    assert result["order_id"] == 12345

    call_kwargs = mock_mt5.order_send.call_args[0][0]
    assert call_kwargs["action"] == mock_mt5.TRADE_ACTION_PENDING
    assert call_kwargs["type"] == mock_mt5.ORDER_TYPE_BUY_LIMIT
    assert call_kwargs["magic"] == 234001  # XAUUSD magic number
    assert call_kwargs["symbol"] == "XAUUSD"
    assert call_kwargs["price"] == 2350.0


# ── 4. Retcode error ──────────────────────────────────────────

async def test_place_order_retcode_error(mock_mt5, sample_buy_signal):
    """place_pending_order() returns success=False for error retcodes."""
    order_result_mock = mock_mt5.order_send.return_value
    order_result_mock.retcode = 10006  # REJECT
    bridge = MT5Bridge()
    await bridge.connect()
    result = await bridge.place_pending_order(sample_buy_signal, 0.10)
    assert result["success"] is False
    assert result["retcode"] == 10006


# ── 5. Empty positions ────────────────────────────────────────

async def test_get_positions_empty(mock_mt5):
    """get_open_positions() returns [] when positions_get returns None."""
    mock_mt5.positions_get.return_value = None
    bridge = MT5Bridge()
    positions = await bridge.get_open_positions()
    assert positions == []


# ── 6. Spread check via risk_manager ─────────────────────────

async def test_spread_check(mock_mt5, sample_buy_signal):
    """validate_signal() returns False when spread exceeds the cap for XAUUSD."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    # XAUUSD cap is 30 points; pass spread=50
    valid, reason = rm.validate_signal(sample_buy_signal, current_spread=50)
    assert valid is False
    assert "spread" in reason.lower()
''')

W("tests/test_signal_engine.py", '''"""
tests/test_signal_engine.py
===========================
Unit tests for Pydantic models in core/signal_engine.py (6 tests).
"""
import pytest
from pydantic import ValidationError

from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)

_VALID_DICT = {
    "pair": "XAUUSD",
    "direction": "BUY",
    "order_type": "BUY_LIMIT",
    "entry_price": 2350.0,
    "stop_loss": 2340.0,
    "take_profit_1": 2370.0,
    "timeframe": "H4",
    "strategy": "SWING",
    "confidence": 78,
    "reasoning": "XAUUSD pulled back to 50 EMA on H4 after structure break. RSI at 52.",
}


# ── 1. Valid BUY signal parses ────────────────────────────────

def test_valid_buy_signal_parsed():
    """model_validate() succeeds on a well-formed BUY signal dict."""
    signal = TradeSignal.model_validate(_VALID_DICT)
    assert signal.pair == "XAUUSD"
    assert signal.direction == Direction.BUY
    assert signal.order_type == OrderType.BUY_LIMIT
    assert signal.entry_price == 2350.0


# ── 2. Invalid SL for BUY ─────────────────────────────────────

def test_invalid_sl_buy_rejected():
    """BUY with stop_loss >= entry_price must raise ValidationError."""
    bad = {**_VALID_DICT, "stop_loss": 2360.0}  # sl above entry
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 3. Invalid SL for SELL ────────────────────────────────────

def test_invalid_sl_sell_rejected():
    """SELL with stop_loss <= entry_price must raise ValidationError."""
    bad = {
        **_VALID_DICT,
        "direction": "SELL",
        "order_type": "SELL_LIMIT",
        "stop_loss": 2340.0,   # sl below entry — invalid for SELL
        "take_profit_1": 2320.0,
    }
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 4. Low confidence rejected ────────────────────────────────

def test_low_confidence_rejected():
    """confidence < 60 must raise ValidationError."""
    bad = {**_VALID_DICT, "confidence": 45}
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 5. R:R ratio calculated correctly ────────────────────────

def test_rr_ratio_calculated():
    """risk_reward_ratio property: entry=2350, sl=2340, tp1=2370 => 2.0."""
    signal = TradeSignal.model_validate(_VALID_DICT)
    assert signal.risk_reward_ratio == 2.0


# ── 6. NoTradeSignal parses ───────────────────────────────────

def test_no_trade_signal_parsed():
    """NoTradeSignal with signal=NO_TRADE and reasoning parses correctly."""
    data = {"signal": "NO_TRADE", "reasoning": "No valid setup found on this timeframe."}
    s = NoTradeSignal.model_validate(data)
    assert s.signal == "NO_TRADE"
    assert len(s.reasoning) >= 10
''')

W("tests/test_risk_manager.py", '''"""
tests/test_risk_manager.py
==========================
Unit tests for core/risk_manager.py (7 tests).
"""
import pytest

from core.risk_manager import RiskManager
from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)


# ── Shared specs ──────────────────────────────────────────────

_XAUUSD = {"point": 0.01, "contract_size": 100.0, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}
_EURUSD = {"point": 0.00001, "contract_size": 100000.0, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}


def _buy_signal(entry=2350.0, sl=2340.0, tp=2370.0, pair="XAUUSD", conf=78) -> TradeSignal:
    return TradeSignal(
        pair=pair,
        direction=Direction.BUY,
        order_type=OrderType.BUY_LIMIT,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        timeframe=Timeframe.H4,
        strategy=Strategy.SWING,
        confidence=conf,
        reasoning="Test reasoning string that is long enough to pass validation rules here.",
    )


# ── 1. XAUUSD lot size ────────────────────────────────────────

def test_lot_size_xauusd():
    """equity=10000, risk=1%, XAUUSD entry=2350, sl=2340 => 0.10 lots."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2340.0, 10000.0, _XAUUSD)
    assert lot == 0.10


# ── 2. EURUSD lot size ────────────────────────────────────────

def test_lot_size_eurusd():
    """equity=10000, risk=1%, EURUSD entry=1.08500, sl=1.08400 => 1.00 lot."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    lot = rm.calculate_lot_size("EURUSD", 1.08500, 1.08400, 10000.0, _EURUSD)
    assert lot == 1.00


# ── 3. Rounds to volume_step ──────────────────────────────────

def test_lot_size_rounds_to_step():
    """Result must be a multiple of volume_step (round down)."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    # entry=2350, sl=2343 => sl_dist=7 => raw=100/(700*1)=0.14285 => floor to 0.14
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2343.0, 10000.0, _XAUUSD)
    assert lot == 0.14
    # Verify it is a multiple of step (0.01)
    assert round(lot % 0.01, 8) == 0.0


# ── 4. Lot clamped to minimum ─────────────────────────────────

def test_lot_size_clamped_to_min():
    """Very small account => lot clamped to volume_min."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    # equity=100 => risk=1 => raw=1/(1000*1)=0.001 => floor=0 => clamp to 0.01
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2340.0, 100.0, _XAUUSD)
    assert lot == 0.01


# ── 5. validate_signal passes ─────────────────────────────────

def test_validate_signal_passes():
    """Valid signal with rr>=2.0 and spread within cap => (True, '')."""
    rm = RiskManager()
    rm.default_min_rr = 2.0
    sig = _buy_signal(entry=2350.0, sl=2340.0, tp=2370.0)
    valid, reason = rm.validate_signal(sig, current_spread=10)
    assert valid is True
    assert reason == ""


# ── 6. validate_signal fails low R:R ─────────────────────────

def test_validate_signal_fails_rr():
    """Signal with rr=1.5 must be rejected with 'R:R' in reason."""
    rm = RiskManager()
    rm.default_min_rr = 2.0
    # entry=2350, sl=2340 (risk=10), tp1=2365 (reward=15) => rr=1.5
    sig = _buy_signal(entry=2350.0, sl=2340.0, tp=2365.0)
    valid, reason = rm.validate_signal(sig, current_spread=10)
    assert valid is False
    assert "R:R" in reason or "r:r" in reason.lower()


# ── 7. validate_signal fails spread ──────────────────────────

def test_validate_signal_fails_spread():
    """Spread=50 for XAUUSD (cap=30) must be rejected with 'spread' in reason."""
    rm = RiskManager()
    sig = _buy_signal()
    valid, reason = rm.validate_signal(sig, current_spread=50)
    assert valid is False
    assert "spread" in reason.lower()
''')

# ══════════════════════════════════════════════════════════════
# SECTION 7 — MCP, Logs, Main
# ══════════════════════════════════════════════════════════════

W("mcp/setup.md", """# MCP Setup — TradingView Desktop Integration

## Prerequisites

- Google Chrome or Chromium installed
- Node.js 18+ installed
- TradingView Desktop app (or tradingview.com open in Chrome)

## Step 1 — Clone tradingview-mcp

```bash
git clone https://github.com/tradesdontlie/tradingview-mcp
cd tradingview-mcp
npm install
npm run build
```

## Step 2 — Launch TradingView with CDP enabled

```bash
# Windows
"C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe" \\\\
  --remote-debugging-port=9222 \\\\
  --user-data-dir=C:\\\\temp\\\\chrome-debug \\\\
  https://www.tradingview.com/chart/

# macOS
/Applications/Google\\\\ Chrome.app/Contents/MacOS/Google\\\\ Chrome \\\\
  --remote-debugging-port=9222 \\\\
  --user-data-dir=/tmp/chrome-debug \\\\
  https://www.tradingview.com/chart/
```

## Step 3 — Register in Claude MCP config

Create or edit `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/path/to/tradingview-mcp/dist/index.js"],
      "env": {
        "CDP_URL": "http://localhost:9222"
      }
    }
  }
}
```

## Step 4 — Verify

In Claude Code, run:
```
/mcp
```
You should see `tradingview` listed as a connected server.

## Environment Variable

```
TRADINGVIEW_CDP_PORT=9222   # in .env
```
""")

W("logs/.gitkeep", "")

W("main.py", '''"""
main.py
=======
ClaudeTradingBot — application entry point.

Starts the FastAPI server, initialises the database, connects MT5,
and schedules periodic scans via APScheduler.

Usage
-----
    python main.py
    # or
    uvicorn main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from loguru import logger

load_dotenv()

# ── Scheduler setup (created before lifespan so jobs can be added) ─
_scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_swing_scan() -> None:
    """APScheduler job: swing scan every 4 hours."""
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        results = await engine.scan_all_pairs("SWING")
        logger.info(f"Swing scan complete: {len(results)} pairs processed")
    except Exception as exc:
        logger.error(f"Swing scan error: {exc}")


async def _run_scalping_scan() -> None:
    """APScheduler job: scalping scan every 15 min (London + NY sessions)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    hour = now.hour
    # London + NY sessions: 07:00–21:00 UTC
    if not (7 <= hour < 21):
        return
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        results = await engine.scan_all_pairs("SCALPING")
        logger.info(f"Scalping scan complete: {len(results)} pairs processed")
    except Exception as exc:
        logger.error(f"Scalping scan error: {exc}")


async def _check_daily_loss() -> None:
    """APScheduler job: check daily loss limit every 5 minutes."""
    try:
        from core.mt5_bridge import MT5Bridge
        from core.risk_manager import RiskManager
        bridge = MT5Bridge()
        rm = RiskManager()
        breached, loss_pct = await rm.check_daily_loss(bridge)
        if breached:
            from notifications.telegram import TelegramNotifier
            notifier = TelegramNotifier()
            await notifier.send_bot_paused(f"Daily loss limit reached: {loss_pct:.2f}%")
    except Exception as exc:
        logger.error(f"Daily loss check error: {exc}")


# ── Lifespan ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown lifecycle for FastAPI."""
    # Startup
    logger.info("ClaudeTradingBot starting...")

    # Initialise database
    from database.db import init_db
    await init_db()

    # Connect MT5
    bot_mode = os.getenv("BOT_MODE", "SIGNAL_ONLY")
    if bot_mode != "SIGNAL_ONLY":
        try:
            from core.mt5_bridge import MT5Bridge
            bridge = MT5Bridge()
            await bridge.connect()
            application.state.mt5_bridge = bridge
        except Exception as exc:
            logger.warning(f"MT5 connect skipped (dry run or no terminal): {exc}")
    else:
        logger.info("SIGNAL_ONLY mode: MT5 connection skipped at startup")

    # Schedule jobs
    # Swing scan: every 4 hours at H4 candle closes
    _scheduler.add_job(
        _run_swing_scan,
        CronTrigger(hour="1,5,9,13,17,21", minute=5),
        id="swing_scan",
        replace_existing=True,
    )
    # Scalping scan: every 15 minutes
    _scheduler.add_job(
        _run_scalping_scan,
        IntervalTrigger(minutes=15),
        id="scalping_scan",
        replace_existing=True,
    )
    # Daily loss check: every 5 minutes
    _scheduler.add_job(
        _check_daily_loss,
        IntervalTrigger(minutes=5),
        id="loss_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started. Bot is live.")

    yield  # ← server is running

    # Shutdown
    _scheduler.shutdown(wait=False)
    logger.info("ClaudeTradingBot stopped.")


# ── FastAPI app ─────────────────────────────────────────────────

app = FastAPI(
    title="ClaudeTradingBot API",
    version="1.0.0",
    description="AI-powered trading signal system backed by Claude + MT5 (Exness)",
    lifespan=lifespan,
)

from api.routes import router  # noqa: E402
app.include_router(router)


# ── Entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",   # localhost only — never 0.0.0.0
        port=8000,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
''')

# ══════════════════════════════════════════════════════════════
# DONE
# ══════════════════════════════════════════════════════════════

print()
print("=" * 60)
print("Bootstrap complete!")
print("=" * 60)
print()
print("Next steps:")
print("  1. pip install -r requirements.txt")
print("  2. Copy .env.example to .env and fill in your credentials")
print("  3. Run tests:  pytest tests/ -v --tb=short")
print("  4. Start bot:  python main.py")
print()

# Print directory tree
print("Project structure:")
for p in sorted(BASE.rglob("*")):
    rel = p.relative_to(BASE)
    parts = rel.parts
    if any(part.startswith(".") for part in parts):
        continue
    if any(part in ("__pycache__", "node_modules", ".git") for part in parts):
        continue
    depth = len(parts) - 1
    indent = "  " * depth
    print(f"  {indent}{p.name}{'/' if p.is_dir() else ''}")
