"""
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
