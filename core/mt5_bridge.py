"""
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
    "XAUUSDm": {"point": 0.01,    "contract_size": 100,    "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "BTCUSDm": {"point": 0.01,    "contract_size": 1,      "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "EURUSDm": {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "GBPUSDm": {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "USDJPYm": {"point": 0.001,   "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    # Legacy names kept as aliases
    "XAUUSD":  {"point": 0.01,    "contract_size": 100,    "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "BTCUSD":  {"point": 0.01,    "contract_size": 1,      "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "EURUSD":  {"point": 0.00001, "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
    "USDJPY":  {"point": 0.001,   "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01},
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
            # Ensure symbol is visible in Market Watch
            await asyncio.to_thread(mt5.symbol_select, symbol, True)
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

    async def modify_order(
        self,
        ticket: int,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> dict:
        """Modify price, SL, or TP of an existing pending order."""
        orders = await asyncio.to_thread(mt5.orders_get)
        target = next((o for o in (orders or []) if o.ticket == ticket), None)
        if target is None:
            return {"success": False, "message": f"Order {ticket} not found"}

        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": price if price is not None else target.price_open,
            "sl": stop_loss if stop_loss is not None else target.sl,
            "tp": take_profit if take_profit is not None else target.tp,
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
                "ticket": ticket,
            }
        except Exception as exc:
            logger.error(f"modify_order({ticket}) error: {exc}")
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
