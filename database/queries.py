"""
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
