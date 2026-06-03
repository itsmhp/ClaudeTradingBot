"""
core/feedback_loop.py
=====================
Reads past trade performance from the database and injects a plain-English
performance summary into Claude's system prompt so the AI is aware of what
strategies and pairs are working — and which to avoid.

Usage in ClaudeClient:
    context = await feedback_loop.build_performance_context(days=30)
    # Append `context` to system prompt before calling Claude API
"""
from __future__ import annotations

from loguru import logger


class FeedbackLoop:
    """Provides performance-based context strings for Claude's system prompt."""

    def __init__(self, db_session_factory=None) -> None:
        self._db_session_factory = db_session_factory

    async def get_recent_performance_by_pair(self, days: int = 30) -> dict[str, dict]:
        """Query executed_trades for last N days, compute per-symbol stats.

        Returns dict keyed by symbol:
            {"wins": int, "losses": int, "total": int, "net_pnl": float, "win_rate": float}
        """
        try:
            from datetime import datetime, timedelta

            from sqlalchemy import select

            from database.db import get_session
            from database.models import ExecutedTrade

            cutoff = datetime.utcnow() - timedelta(days=days)
            result: dict[str, dict] = {}

            async with get_session() as session:
                stmt = select(ExecutedTrade).where(
                    ExecutedTrade.created_at >= cutoff  # type: ignore[attr-defined]
                )
                rows = (await session.execute(stmt)).scalars().all()

                for row in rows:
                    sym = getattr(row, "symbol", "UNKNOWN")
                    if sym not in result:
                        result[sym] = {"wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0}
                    pnl: float = getattr(row, "profit", 0.0) or 0.0
                    result[sym]["total"] += 1
                    result[sym]["net_pnl"] += pnl
                    if pnl > 0:
                        result[sym]["wins"] += 1
                    else:
                        result[sym]["losses"] += 1

            for sym, data in result.items():
                data["win_rate"] = (
                    round(data["wins"] / data["total"] * 100, 1) if data["total"] > 0 else 0.0
                )

            return result

        except Exception as exc:
            logger.warning(f"[FeedbackLoop] DB query failed: {exc}")
            return {}

    async def get_performance_by_setup(self, days: int = 30) -> dict:
        """Group performance by (symbol, strategy, timeframe).

        Returns list of dicts sorted by win rate descending.
        """
        try:
            from datetime import datetime, timedelta

            from sqlalchemy import select

            from database.db import get_session
            from database.models import ExecutedTrade

            cutoff = datetime.utcnow() - timedelta(days=days)
            buckets: dict[str, dict] = {}

            async with get_session() as session:
                stmt = select(ExecutedTrade).where(
                    ExecutedTrade.created_at >= cutoff  # type: ignore[attr-defined]
                )
                rows = (await session.execute(stmt)).scalars().all()

                for row in rows:
                    sym = getattr(row, "symbol", "UNKNOWN")
                    strat = getattr(row, "strategy", "UNKNOWN")
                    tf = getattr(row, "timeframe", "UNKNOWN")
                    key = f"{sym}:{strat}:{tf}"
                    if key not in buckets:
                        buckets[key] = {"symbol": sym, "strategy": strat, "timeframe": tf,
                                        "wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0}
                    pnl: float = getattr(row, "profit", 0.0) or 0.0
                    buckets[key]["total"] += 1
                    buckets[key]["net_pnl"] += pnl
                    if pnl > 0:
                        buckets[key]["wins"] += 1
                    else:
                        buckets[key]["losses"] += 1

            for key, data in buckets.items():
                data["win_rate"] = (
                    round(data["wins"] / data["total"] * 100, 1) if data["total"] > 0 else 0.0
                )

            return {
                "setups": sorted(
                    buckets.values(),
                    key=lambda x: x.get("win_rate", 0),
                    reverse=True,
                )
            }

        except Exception as exc:
            logger.warning(f"[FeedbackLoop] get_performance_by_setup failed: {exc}")
            return {"setups": []}

    async def build_performance_context(self, days: int = 30) -> str:
        """Generate a plain-English performance summary for injection into Claude's system prompt.

        Example output:
            Recent performance context (last 30 days):
              - XAUUSD: 8 trades, 62% win rate, net P&L $+124.50 — PERFORMING BELOW TARGET
              - EURUSD: 15 trades, 73% win rate, net P&L $+312.00 — PERFORMING WELL
              ⚠ Avoid: BTCUSD signals (low win rate)
              ★ Prioritize: GBPUSD (high win rate)
        """
        perf = await self.get_recent_performance_by_pair(days)
        if not perf:
            return ""

        lines = [f"\nRecent performance context (last {days} days):"]
        for sym, data in sorted(perf.items()):
            wr = data.get("win_rate", 0.0)
            total = data.get("total", 0)
            pnl = data.get("net_pnl", 0.0)
            status = "PERFORMING WELL" if wr >= 60 else "PERFORMING BELOW TARGET"
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"  - {sym}: {total} trades, {wr:.0f}% win rate, "
                f"net P&L ${sign}{pnl:.2f} — {status}"
            )

        sorted_by_wr = sorted(perf.items(), key=lambda x: x[1].get("win_rate", 0))
        if sorted_by_wr:
            worst_sym, worst_data = sorted_by_wr[0]
            best_sym, best_data = sorted_by_wr[-1]
            if worst_data.get("win_rate", 100) < 40 and worst_data.get("total", 0) >= 3:
                lines.append(f"  ⚠ Avoid: {worst_sym} signals (only {worst_data['win_rate']:.0f}% win rate)")
            if best_data.get("win_rate", 0) >= 70 and best_data.get("total", 0) >= 3:
                lines.append(f"  ★ Prioritize: {best_sym} ({best_data['win_rate']:.0f}% win rate)")

        return "\n".join(lines)

    async def should_reduce_size_for_pair(self, symbol: str) -> tuple[bool, float]:
        """Check if recent 14-day performance warrants reducing lot size.

        Returns:
            (should_reduce: bool, suggested_multiplier: float)
            e.g. (True, 0.5) → trade half the normal lot size
        """
        perf = await self.get_recent_performance_by_pair(days=14)
        data = perf.get(symbol, {})
        wr = data.get("win_rate", 50.0)
        pnl = data.get("net_pnl", 0.0)
        if wr < 40 or pnl < 0:
            logger.info(
                f"[FeedbackLoop] Reducing lot size for {symbol} "
                f"(win_rate={wr:.0f}%, pnl=${pnl:.2f})"
            )
            return (True, 0.5)
        return (False, 1.0)
