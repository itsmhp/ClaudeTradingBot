"""
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

        # Inject performance context from FeedbackLoop (Phase 5)
        system_prompt = self._SYSTEM_PROMPT
        try:
            from core.feedback_loop import FeedbackLoop
            fl = FeedbackLoop()
            perf_context = await fl.build_performance_context(days=30)
            if perf_context:
                system_prompt = system_prompt + "\n\n" + perf_context
        except Exception:
            pass  # FeedbackLoop is non-critical

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
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
        pairs_summary = "\n".join(
            f"  {pair}: price={data.get('price', 'N/A')}, rsi={data.get('rsi', 'N/A')}"
            for pair, data in chart_data_map.items()
        )
        prompt = (
            "Generate a concise daily trading briefing covering market bias "
            "(bullish/bearish/ranging) for each pair below. "
            "Keep it under 300 words.\n\n" + pairs_summary
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
