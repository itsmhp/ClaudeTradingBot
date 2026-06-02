"""
multi_account/copy_engine.py
=============================
Copy trading engine — mirrors master signals to follower accounts.

When the master places a trade, CopyEngine calculates each follower's
lot size (proportional or risk-based), applies copy_delay_seconds, then
places the identical pending order on each follower account.

Usage::

    copy_engine = CopyEngine(account_manager, registry)
    results = await copy_engine.copy_signal_to_followers(signal, master_lot=0.1, master_order_id=123456)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from .account_manager import AccountManager
from .account_registry import AccountRegistry, MT5Account

# ── Optional SQLAlchemy ──────────────────────────────────────────────────────
try:
    from sqlalchemy import Column, DateTime, Float, Integer, String
    from database.db import Base
    SA_AVAILABLE = True
except ImportError:
    SA_AVAILABLE = False
    Base = object  # type: ignore[assignment,misc]

# ── Signal schema ────────────────────────────────────────────────────────────
try:
    from core.signal_engine import TradeSignal
    SIGNAL_AVAILABLE = True
except ImportError:
    TradeSignal = None  # type: ignore[assignment,misc]
    SIGNAL_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy model for copy_trades table
# ══════════════════════════════════════════════════════════════════════════════

if SA_AVAILABLE:
    class CopyTradeRecord(Base):  # type: ignore[valid-type,misc]
        """Persisted record of a copied trade."""
        __tablename__ = "copy_trades"
        __table_args__ = {"extend_existing": True}

        id                   = Column(Integer, primary_key=True, autoincrement=True)
        master_signal_id     = Column(Integer, nullable=True)
        master_order_id      = Column(Integer, nullable=True)
        follower_account_id  = Column(String(64), nullable=False)
        follower_order_id    = Column(Integer, nullable=True)
        symbol               = Column(String(20), nullable=False)
        direction            = Column(String(10), nullable=False)
        lot_size             = Column(Float, default=0.01)
        status               = Column(String(20), default="PENDING")
        profit               = Column(Float, nullable=True)
        error                = Column(String(256), nullable=True)
        created_at           = Column(DateTime, default=lambda: datetime.now(timezone.utc))
        updated_at           = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))
else:
    CopyTradeRecord = None  # type: ignore[assignment,misc]


# ══════════════════════════════════════════════════════════════════════════════
# CopyEngine
# ══════════════════════════════════════════════════════════════════════════════

class CopyEngine:
    """Copy master trades to all active follower accounts.

    Parameters
    ----------
    account_manager:
        :class:`AccountManager` with active connections.
    registry:
        :class:`AccountRegistry` for follower metadata.
    db_session:
        Optional SQLAlchemy session for logging copy trade records.
    """

    def __init__(
        self,
        account_manager: AccountManager,
        registry: AccountRegistry,
        db_session=None,
    ) -> None:
        self._mgr = account_manager
        self._reg = registry
        self._db  = db_session

    # ── Core copy logic ──────────────────────────────────────────────────────

    async def copy_signal_to_followers(
        self,
        signal: Any,            # TradeSignal or dict
        master_lot_size: float,
        master_order_id: int,
    ) -> list[dict]:
        """Copy a master trade signal to all active follower accounts.

        Parameters
        ----------
        signal:
            :class:`TradeSignal` or dict with trade parameters.
        master_lot_size:
            Lot size actually used on the master account.
        master_order_id:
            MT5 ticket/order ID of the master order.

        Returns
        -------
        list of dicts: ``[{account_id, success, order_id, lot_size, error}]``
        """
        followers = self._reg.get_followers()
        if not followers:
            logger.info("[CopyEngine] No active followers — nothing to copy")
            return []

        # Extract signal fields (support both TradeSignal and plain dict)
        if hasattr(signal, "dict"):
            sig = signal.dict()
        elif isinstance(signal, dict):
            sig = signal
        else:
            sig = {}

        symbol    = sig.get("symbol", "")
        direction = sig.get("direction", "")

        tasks = [
            self._copy_to_follower(sig, follower, master_lot_size, master_order_id)
            for follower in followers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final: list[dict] = []
        for follower, result in zip(followers, results):
            if isinstance(result, Exception):
                logger.error(f"[CopyEngine] {follower.account_id} exception: {result}")
                final.append({
                    "account_id": follower.account_id,
                    "success": False,
                    "order_id": None,
                    "lot_size": 0,
                    "error": str(result),
                })
            else:
                final.append(result)

        success_count = sum(1 for r in final if r.get("success"))
        fail_count    = len(final) - success_count
        logger.info(
            f"[CopyEngine] {symbol} {direction} copied to {len(followers)} accounts "
            f"— {success_count} success / {fail_count} failed"
        )
        return final

    async def copy_cancel_to_followers(
        self,
        master_ticket: int,
    ) -> list[dict]:
        """Cancel all follower orders linked to a master ticket.

        Returns
        -------
        list of dicts: ``[{account_id, success, follower_ticket, error}]``
        """
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            logger.warning("[CopyEngine] No DB session — cannot look up copy records")
            return []

        records = (
            self._db.query(CopyTradeRecord)
            .filter_by(master_order_id=master_ticket)
            .all()
        )

        results: list[dict] = []
        for rec in records:
            if not rec.follower_order_id:
                continue
            try:
                bridge = self._mgr.get_bridge(rec.follower_account_id)
                success = bridge.cancel_order(rec.follower_order_id)
                if success:
                    rec.status = "CANCELLED"
                    self._db.commit()
                results.append({
                    "account_id":      rec.follower_account_id,
                    "follower_ticket": rec.follower_order_id,
                    "success":         success,
                    "error":           None,
                })
            except Exception as exc:
                results.append({
                    "account_id":      rec.follower_account_id,
                    "follower_ticket": rec.follower_order_id,
                    "success":         False,
                    "error":           str(exc),
                })

        return results

    def get_copy_performance(self) -> dict:
        """Compare master vs each follower: net P&L, win rate, trade count.

        Returns a comparison dict.
        """
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            return {"error": "Database not available"}

        records = self._db.query(CopyTradeRecord).all()
        by_account: dict[str, dict] = {}

        for rec in records:
            aid = rec.follower_account_id
            if aid not in by_account:
                by_account[aid] = {"trades": 0, "wins": 0, "total_profit": 0.0}
            by_account[aid]["trades"] += 1
            profit = rec.profit or 0
            by_account[aid]["total_profit"] += profit
            if profit > 0:
                by_account[aid]["wins"] += 1

        comparison: list[dict] = []
        for aid, stats in by_account.items():
            t = stats["trades"]
            comparison.append({
                "account_id":  aid,
                "trades":      t,
                "win_rate":    round(stats["wins"] / t, 4) if t else 0,
                "net_profit":  round(stats["total_profit"], 2),
            })

        return {
            "followers":   comparison,
            "total_copied_trades": len(records),
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _copy_to_follower(
        self,
        sig: dict,
        follower: MT5Account,
        master_lot_size: float,
        master_order_id: int,
    ) -> dict:
        """Execute copy for a single follower account."""
        # Apply copy delay
        if follower.copy_delay_seconds > 0:
            await asyncio.sleep(follower.copy_delay_seconds)

        if not self._mgr.is_connected(follower.account_id):
            return {
                "account_id": follower.account_id,
                "success": False,
                "order_id": None,
                "lot_size": 0,
                "error": "Not connected",
            }

        # Calculate follower lot size
        follower_lot = round(master_lot_size * follower.lot_size_multiplier, 2)
        follower_lot = max(follower_lot, 0.01)  # MT5 minimum

        bridge = self._mgr.get_bridge(follower.account_id)

        # Build order comment linking back to master
        comment = f"CTB_COPY_{master_order_id}"

        try:
            order_result = bridge.place_pending_order_dict(
                symbol     = sig.get("symbol", ""),
                direction  = sig.get("direction", "BUY"),
                order_type = sig.get("order_type", "BUY_LIMIT"),
                entry      = sig.get("entry", 0),
                stop_loss  = sig.get("stop_loss", 0),
                take_profit= sig.get("take_profit_1", 0),
                lot_size   = follower_lot,
                magic      = 234000 + follower.magic_number_offset,
                comment    = comment,
            )
            success   = bool(order_result and order_result.get("retcode") == 10009)
            order_id  = order_result.get("order") if order_result else None

            # Persist to copy_trades
            self._log_copy_trade(
                sig=sig,
                follower=follower,
                master_order_id=master_order_id,
                follower_order_id=order_id,
                lot_size=follower_lot,
                success=success,
                error=None if success else str(order_result),
            )

            return {
                "account_id": follower.account_id,
                "success":    success,
                "order_id":   order_id,
                "lot_size":   follower_lot,
                "error":      None if success else str(order_result),
            }
        except Exception as exc:
            self._log_copy_trade(
                sig=sig,
                follower=follower,
                master_order_id=master_order_id,
                follower_order_id=None,
                lot_size=follower_lot,
                success=False,
                error=str(exc),
            )
            return {
                "account_id": follower.account_id,
                "success":    False,
                "order_id":   None,
                "lot_size":   follower_lot,
                "error":      str(exc),
            }

    def _log_copy_trade(
        self,
        sig: dict,
        follower: MT5Account,
        master_order_id: int,
        follower_order_id: Optional[int],
        lot_size: float,
        success: bool,
        error: Optional[str],
    ) -> None:
        """Write a CopyTradeRecord to the database."""
        if self._db is None or not SA_AVAILABLE or CopyTradeRecord is None:
            return
        try:
            rec = CopyTradeRecord(
                master_order_id     = master_order_id,
                follower_account_id = follower.account_id,
                follower_order_id   = follower_order_id,
                symbol              = sig.get("symbol", ""),
                direction           = sig.get("direction", ""),
                lot_size            = lot_size,
                status              = "FILLED" if success else "FAILED",
                error               = error,
            )
            self._db.add(rec)
            self._db.commit()
        except Exception as exc:
            logger.warning(f"[CopyEngine] DB log error: {exc}")
