"""
core/news_monitor.py
====================
Monitors the economic calendar and fetches news sentiment.

Sources:
  1. ForexFactory JSON calendar (free, no API key required)
     URL: https://nfs.faireconomy.media/ff_calendar_thisweek.json
  2. NewsAPI (newsapi.org) — optional, requires NEWS_API_KEY

Responsibilities:
  - fetch_economic_calendar(): get high-impact events in the next N hours
  - is_news_blackout(symbol): check if any event is within N minutes
  - fetch_market_sentiment(symbol): get bullish/bearish/neutral score via Claude mini-prompt

Cache: calendar data cached for 30 minutes to avoid excessive requests.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

# Currency → symbols mapping for news blackout
CURRENCY_TO_SYMBOLS: dict[str, list[str]] = {
    "USD": ["XAUUSD", "BTCUSD", "USDJPY", "EURUSD", "GBPUSD", "NAS100", "US30"],
    "EUR": ["EURUSD"],
    "GBP": ["GBPUSD"],
    "JPY": ["USDJPY"],
    "BTC": ["BTCUSD"],
    "XAU": ["XAUUSD"],
}

# Search queries per symbol for NewsAPI
SYMBOL_QUERIES: dict[str, str] = {
    "XAUUSD": "gold price",
    "BTCUSD": "bitcoin price",
    "USDJPY": "USD JPY dollar yen",
    "EURUSD": "EUR USD euro dollar",
    "GBPUSD": "GBP USD pound sterling",
    "NAS100": "Nasdaq 100 technology stocks",
    "US30": "Dow Jones stock market",
}


class NewsMonitor:
    """Fetches economic calendar and news sentiment for trading pairs."""

    CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    CACHE_MINUTES = 30

    def __init__(self) -> None:
        self._news_api_key: str = os.getenv("NEWS_API_KEY", "")
        self._calendar_cache: Optional[list[dict]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._session = None

    async def _get_session(self):
        """Lazy-create aiohttp session."""
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                )
            except ImportError:
                logger.warning("[NewsMonitor] aiohttp not installed")
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _is_cache_valid(self) -> bool:
        if self._calendar_cache is None or self._cache_timestamp is None:
            return False
        age = datetime.now(timezone.utc) - self._cache_timestamp
        return age.total_seconds() < self.CACHE_MINUTES * 60

    async def fetch_economic_calendar(self, hours_ahead: int = 4) -> list[dict]:
        """Fetch high-impact economic events from ForexFactory.

        Returns list of:
            {"event": str, "time_utc": datetime, "currency": str, "impact": str}

        Results are cached for 30 minutes.
        """
        if self._is_cache_valid() and self._calendar_cache is not None:
            return self._filter_upcoming(self._calendar_cache, hours_ahead)

        session = await self._get_session()
        if session is None:
            return []

        try:
            async with session.get(self.CALENDAR_URL) as resp:
                if resp.status != 200:
                    logger.warning(f"[NewsMonitor] Calendar API returned {resp.status}")
                    return []
                raw = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[NewsMonitor] Calendar fetch failed: {exc}")
            return []

        events: list[dict] = []
        for item in raw:
            try:
                if item.get("impact", "").lower() != "high":
                    continue
                date_str = item.get("date", "") or item.get("time", "")
                try:
                    event_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                events.append({
                    "event": item.get("title", item.get("name", "Unknown event")),
                    "time_utc": event_time,
                    "currency": item.get("country", item.get("currency", "USD")).upper(),
                    "impact": "High",
                })
            except Exception:
                continue

        self._calendar_cache = events
        self._cache_timestamp = datetime.now(timezone.utc)
        logger.info(f"[NewsMonitor] Loaded {len(events)} high-impact events from ForexFactory")
        return self._filter_upcoming(events, hours_ahead)

    def _filter_upcoming(self, events: list[dict], hours_ahead: int) -> list[dict]:
        """Filter events within the next `hours_ahead` hours from now."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in events if now <= e["time_utc"] <= cutoff]

    async def is_news_blackout(
        self, symbol: str, minutes_before: int = 30
    ) -> tuple[bool, str]:
        """Check if there is a high-impact news event within `minutes_before` minutes.

        Returns:
            (is_blackout: bool, event_name: str)
        """
        # BTCUSD is not affected by forex news
        if symbol == "BTCUSD":
            return (False, "")

        events = await self.fetch_economic_calendar(hours_ahead=4)
        now = datetime.now(timezone.utc)
        window = timedelta(minutes=minutes_before)

        for event in events:
            event_time = event["time_utc"]
            currency = event["currency"]
            time_until = event_time - now

            if timedelta(0) <= time_until <= window:
                # Check if this currency affects the symbol
                affected = CURRENCY_TO_SYMBOLS.get(currency, [])
                if symbol in affected or currency in symbol:
                    mins = int(time_until.total_seconds() / 60)
                    logger.info(
                        f"[NewsMonitor] Blackout for {symbol}: {event['event']} in {mins}m"
                    )
                    return (True, f"{event['event']} in {mins} minutes")

        return (False, "")

    async def fetch_market_sentiment(self, symbol: str) -> dict:
        """Fetch recent news headlines and classify sentiment via Claude mini-prompt.

        Returns:
            {"sentiment": "BULLISH"|"BEARISH"|"NEUTRAL", "score": float, "summary": str}
        """
        neutral_fallback = {"sentiment": "NEUTRAL", "score": 0.0, "summary": "No data", "headlines_count": 0}

        if not self._news_api_key:
            return neutral_fallback

        query = SYMBOL_QUERIES.get(symbol, symbol)
        session = await self._get_session()
        if session is None:
            return neutral_fallback

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        url = (
            f"https://newsapi.org/v2/everything"
            f"?q={query.replace(' ', '+')}"
            f"&from={yesterday}"
            f"&sortBy=popularity"
            f"&pageSize=10"
            f"&apiKey={self._news_api_key}"
        )

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"[NewsMonitor] NewsAPI returned {resp.status}")
                    return neutral_fallback
                data = await resp.json()
        except Exception as exc:
            logger.warning(f"[NewsMonitor] NewsAPI fetch failed for {symbol}: {exc}")
            return neutral_fallback

        articles = data.get("articles", [])
        if not articles:
            return neutral_fallback

        headlines = "\n".join(
            f"- {a.get('title', '')}" for a in articles[:10] if a.get("title")
        )

        # Mini Claude prompt for sentiment classification
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=100,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Given these recent news headlines about {symbol}, "
                        "rate the market sentiment. "
                        'Respond with ONLY JSON: {"sentiment": "BULLISH"|"BEARISH"|"NEUTRAL", '
                        '"score": <-1.0 to 1.0>, "summary": "<one sentence>"}\n\n'
                        f"Headlines:\n{headlines}"
                    ),
                }],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            result = json.loads(raw)
            result["headlines_count"] = len(articles)
            return result
        except Exception as exc:
            logger.warning(f"[NewsMonitor] Sentiment classification failed for {symbol}: {exc}")
            return neutral_fallback

    async def get_upcoming_events_formatted(self, hours_ahead: int = 24) -> list[dict]:
        """Return upcoming events with human-readable time delta for the dashboard."""
        events = await self.fetch_economic_calendar(hours_ahead=hours_ahead)
        now = datetime.now(timezone.utc)
        formatted = []
        for e in events:
            delta = e["time_utc"] - now
            total_secs = int(delta.total_seconds())
            hrs, rem = divmod(total_secs, 3600)
            mins = rem // 60
            time_label = f"in {hrs}h {mins}m" if hrs > 0 else f"in {mins}m"
            affected = CURRENCY_TO_SYMBOLS.get(e["currency"], [])
            formatted.append({
                "event": e["event"],
                "currency": e["currency"],
                "impact": e["impact"],
                "time_utc": e["time_utc"].isoformat(),
                "time_relative": time_label,
                "affected_pairs": affected,
            })
        return formatted
