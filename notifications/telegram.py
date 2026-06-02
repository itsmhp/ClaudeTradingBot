"""
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

        emoji_dir = "\U0001F7E2" if signal.direction == Direction.BUY else "\U0001F534"
        dir_label = "BUY" if signal.direction == Direction.BUY else "SELL"
        arrow = "\U0001F4C8" if signal.direction == Direction.BUY else "\U0001F4C9"

        if bot_mode == "AUTO_EXECUTE" and execution_result and execution_result.get("success"):
            mode_tag = "\u2705 EXECUTING"
            order_line = f"\n\U0001F4CB Order #: {execution_result.get('order_id', 'N/A')}"
        else:
            mode_tag = "\U0001F4E1 SIGNAL ONLY"
            order_line = ""

        tp2_line = (
            f"\n\U0001F3AF TP2: {signal.take_profit_2}"
            if signal.take_profit_2
            else "\n\U0001F3AF TP2: \u2014"
        )

        message = (
            f"{emoji_dir} {dir_label} SIGNAL \u2014 {signal.pair}\n\n"
            f"\U0001F4CA Strategy: {signal.strategy.value} | Timeframe: {signal.timeframe.value}\n"
            f"{arrow} Direction: {signal.order_type.value}\n\n"
            f"\U0001F4B0 Entry: {signal.entry_price}\n"
            f"\U0001F6D1 Stop Loss: {signal.stop_loss}\n"
            f"\U0001F3AF TP1: {signal.take_profit_1}"
            f"{tp2_line}\n\n"
            f"\u2696\uFE0F Risk:Reward = 1:{signal.risk_reward_ratio}\n"
            f"\U0001F4CF Lots: {lot_size}\n"
            f"\U0001F3B2 Confidence: {signal.confidence}%\n\n"
            f"\U0001F4A1 Reasoning:\n{signal.reasoning[:200]}\n\n"
            f"\U0001F916 Mode: {mode_tag}{order_line}\n"
            f"\u23F0 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        )
        await self._send(message)

    async def send_no_trade_alert(
        self, pair: str, timeframe: str, reasoning: str
    ) -> None:
        """Send a brief NO_TRADE notification."""
        msg = f"\u2139\uFE0F NO TRADE \u2014 {pair} ({timeframe})\n{reasoning[:150]}"
        await self._send(msg)

    async def send_error_alert(self, component: str, error: str) -> None:
        """Send an error alert."""
        msg = f"\u26A0\uFE0F ERROR in {component}\n{error[:200]}"
        await self._send(msg)

    async def send_daily_summary(self, performance: dict) -> None:
        """Send end-of-day P&L summary."""
        msg = (
            f"\U0001F4CA Daily Summary\n\n"
            f"Signals  : {performance.get('total_signals', 0)}\n"
            f"Trades   : {performance.get('executed_trades', 0)}\n"
            f"Win Rate : {performance.get('win_rate', 0):.1f}%\n"
            f"Net P&L  : ${performance.get('net_pnl', 0):.2f}\n"
        )
        await self._send(msg)

    async def send_bot_paused(self, reason: str) -> None:
        """Send bot-paused alert."""
        msg = f"\U0001F6D1 BOT PAUSED\n{reason}"
        await self._send(msg)
