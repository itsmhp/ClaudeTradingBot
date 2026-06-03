"""
core/consensus_engine.py
========================
Multi-model consensus engine — runs Claude and GPT-4o-mini in parallel,
compares their signals and only proceeds when both agree.

CONSENSUS_MODE env var (set in .env):
  CLAUDE_ONLY  — use only Claude (default, no OpenAI cost)
  GPT_ONLY     — use only GPT-4o-mini
  CONSENSUS    — run both, require agreement before returning a signal
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Union

from loguru import logger

from core.signal_engine import NoTradeSignal, TradeSignal


class ConsensusEngine:
    """Run Claude + GPT-4o-mini in parallel and compare signals."""

    def __init__(self) -> None:
        self._mode: str = os.getenv("CONSENSUS_MODE", "CLAUDE_ONLY")
        self._gpt_model: str = "gpt-4o-mini"
        self._rules: dict = self._load_rules()
        self._claude_client = None
        self._openai_client = None
        # Stats (in-memory)
        self._total_analyzed: int = 0
        self._agreements: int = 0
        self._disagreements: int = 0

    def _load_rules(self) -> dict:
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        try:
            with open(rules_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_claude_client(self):
        if self._claude_client is None:
            from core.claude_client import ClaudeClient
            self._claude_client = ClaudeClient()
        return self._claude_client

    def _get_openai_client(self):
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI  # type: ignore[import]
                api_key = os.getenv("OPENAI_API_KEY", "")
                if not api_key:
                    logger.warning("[Consensus] OPENAI_API_KEY not set — GPT disabled")
                    return None
                self._openai_client = AsyncOpenAI(api_key=api_key)
            except ImportError:
                logger.warning("[Consensus] openai package not installed — run: pip install openai")
        return self._openai_client

    # ── System prompt (identical to ClaudeClient for fair comparison) ──────────
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

    def _build_user_prompt(self, pair: str, timeframe: str, chart_data: dict) -> str:
        return (
            f"Analyse {pair} on the {timeframe} timeframe.\n\n"
            f"Market Data:\n"
            f"  Price   : {chart_data.get('price', 'N/A')}\n"
            f"  Bid/Ask : {chart_data.get('bid', 'N/A')} / {chart_data.get('ask', 'N/A')}\n"
            f"  Spread  : {chart_data.get('spread', 'N/A')} points\n"
            f"  RSI(14) : {chart_data.get('rsi', 'N/A')}\n"
            f"  EMA 50  : {chart_data.get('ema_50', 'N/A')}\n"
            f"  EMA 200 : {chart_data.get('ema_200', 'N/A')}\n"
            f"  Structure: {chart_data.get('structure', 'N/A')}\n"
            f"  Support : {chart_data.get('support_levels', [])}\n"
            f"  Resistance: {chart_data.get('resistance_levels', [])}\n\n"
            f"Trading Rules (JSON):\n{json.dumps(self._rules, indent=2)}\n\n"
            "Respond with a trade signal JSON or NO_TRADE JSON."
        )

    # ── Per-model analyzers ─────────────────────────────────────────────────────

    async def analyze_chart_claude(
        self, pair: str, timeframe: str, chart_data: dict
    ) -> Union[TradeSignal, NoTradeSignal]:
        """Delegate to ClaudeClient."""
        return await self._get_claude_client().analyze_chart(pair, timeframe, chart_data)

    async def analyze_chart_gpt(
        self, pair: str, timeframe: str, chart_data: dict
    ) -> Union[TradeSignal, NoTradeSignal]:
        """Call GPT-4o-mini with identical prompt format as Claude."""
        client = self._get_openai_client()
        if client is None:
            return NoTradeSignal(reasoning="OpenAI client not available")

        user_prompt = self._build_user_prompt(pair, timeframe, chart_data)
        try:
            response = await client.chat.completions.create(
                model=self._gpt_model,
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=500,
            )
            raw_text: str = response.choices[0].message.content.strip()
        except Exception as exc:
            logger.error(f"[Consensus] GPT error for {pair}: {exc}")
            return NoTradeSignal(reasoning=f"GPT API error: {exc}")

        # Strip markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        try:
            parsed: dict = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning(f"[Consensus] GPT JSON parse failed for {pair}")
            return NoTradeSignal(reasoning="GPT returned invalid JSON")

        if "NO_TRADE" in parsed:
            return NoTradeSignal(reasoning=parsed.get("reasoning", "No setup found"))

        try:
            return TradeSignal.model_validate(parsed)
        except Exception as exc:
            logger.warning(f"[Consensus] GPT TradeSignal validation failed: {exc}")
            return NoTradeSignal(reasoning=f"GPT signal validation error: {exc}")

    # ── Consensus logic ────────────────────────────────────────────────────────

    async def analyze_with_consensus(
        self, pair: str, timeframe: str, chart_data: dict
    ) -> dict:
        """Run both models concurrently and compare their results."""
        self._total_analyzed += 1

        claude_task = asyncio.create_task(
            self.analyze_chart_claude(pair, timeframe, chart_data)
        )
        gpt_task = asyncio.create_task(
            self.analyze_chart_gpt(pair, timeframe, chart_data)
        )
        results = await asyncio.gather(claude_task, gpt_task, return_exceptions=True)
        claude_result = results[0] if not isinstance(results[0], Exception) else None
        gpt_result = results[1] if not isinstance(results[1], Exception) else None

        # Both errored
        if claude_result is None and gpt_result is None:
            return {
                "consensus": "ERROR",
                "agreement": False,
                "claude_signal": None,
                "gpt_signal": None,
            }

        # Single model fallback
        if claude_result is None:
            logger.warning(f"[Consensus] Claude errored for {pair}, using GPT only")
            return {
                "consensus": "single_model",
                "single_model": True,
                "model_used": "GPT",
                "consensus_signal": gpt_result if isinstance(gpt_result, TradeSignal) else None,
                "agreement": False,
                "claude_signal": None,
                "gpt_signal": gpt_result,
            }

        if gpt_result is None:
            logger.warning(f"[Consensus] GPT errored for {pair}, using Claude only")
            return {
                "consensus": "single_model",
                "single_model": True,
                "model_used": "Claude",
                "consensus_signal": claude_result if isinstance(claude_result, TradeSignal) else None,
                "agreement": False,
                "claude_signal": claude_result,
                "gpt_signal": None,
            }

        # Both NO_TRADE
        if isinstance(claude_result, NoTradeSignal) and isinstance(gpt_result, NoTradeSignal):
            self._agreements += 1
            return {"consensus": "NO_TRADE", "agreement": True, "claude_signal": None, "gpt_signal": None}

        # One NO_TRADE, one signal → disagreement
        if isinstance(claude_result, NoTradeSignal) or isinstance(gpt_result, NoTradeSignal):
            self._disagreements += 1
            logger.info(f"[Consensus] Disagreement {pair}: one model says NO_TRADE")
            return {
                "consensus": "DISAGREEMENT",
                "agreement": False,
                "claude_signal": claude_result if isinstance(claude_result, TradeSignal) else None,
                "gpt_signal": gpt_result if isinstance(gpt_result, TradeSignal) else None,
                "reason": "One model returned NO_TRADE",
            }

        # Both signals — compare direction
        claude_sig: TradeSignal = claude_result  # type: ignore[assignment]
        gpt_sig: TradeSignal = gpt_result  # type: ignore[assignment]

        if claude_sig.direction == gpt_sig.direction:
            self._agreements += 1
            boosted = min(95, (claude_sig.confidence + gpt_sig.confidence) // 2 + 5)
            logger.info(f"[Consensus] Agreement {pair}: both say {claude_sig.direction.value}")
            return {
                "consensus": "AGREE",
                "agreement": True,
                "consensus_signal": claude_sig,
                "combined_confidence": boosted,
                "confidence_boost": 5,
                "claude_signal": claude_sig,
                "gpt_signal": gpt_sig,
            }

        self._disagreements += 1
        logger.info(
            f"[Consensus] Disagreement {pair}: Claude={claude_sig.direction.value}, "
            f"GPT={gpt_sig.direction.value}"
        )
        return {
            "consensus": "DISAGREEMENT",
            "agreement": False,
            "claude_signal": claude_sig,
            "gpt_signal": gpt_sig,
            "reason": (
                f"Direction mismatch: Claude={claude_sig.direction.value}, "
                f"GPT={gpt_sig.direction.value}"
            ),
        }

    async def get_consensus_signal(
        self, pair: str, timeframe: str, chart_data: dict
    ) -> Union[TradeSignal, NoTradeSignal]:
        """Entry point — delegates based on CONSENSUS_MODE."""
        if self._mode == "GPT_ONLY":
            return await self.analyze_chart_gpt(pair, timeframe, chart_data)
        elif self._mode == "CONSENSUS":
            result = await self.analyze_with_consensus(pair, timeframe, chart_data)
            if result.get("consensus") == "AGREE" and result.get("consensus_signal"):
                return result["consensus_signal"]
            return NoTradeSignal(
                reasoning=result.get("reason", result.get("consensus", "No consensus"))
            )
        else:  # CLAUDE_ONLY (default)
            return await self.analyze_chart_claude(pair, timeframe, chart_data)

    def get_consensus_stats(self) -> dict:
        """Return agreement/disagreement stats for the dashboard."""
        rate = (
            round(self._agreements / self._total_analyzed * 100, 1)
            if self._total_analyzed > 0
            else 0.0
        )
        return {
            "total_analyzed": self._total_analyzed,
            "agreements": self._agreements,
            "disagreements": self._disagreements,
            "agreement_rate_pct": rate,
            "mode": self._mode,
        }
