"""
core/signal_engine.py
=====================
Pydantic trade-signal models and SignalEngine orchestrator.
Phase 2: broadcasts WebSocket events on signal generation, execution, rejection.

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
    pass


# ─── WebSocket broadcast helper ───────────────────────────────

async def _ws_broadcast(event: str, data: dict) -> None:
    """Non-critical broadcast to WebSocket clients — never raises."""
    try:
        from api.ws_manager import ws_manager
        await ws_manager.broadcast(event, data)
    except Exception:
        pass


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

    pair: str = Field(..., description="Trading instrument symbol", examples=["XAUUSD"])
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
        if v < 60:
            raise ValueError("Confidence below 60% is not actionable")
        return v

    @field_validator("stop_loss")
    @classmethod
    def stop_loss_must_be_valid(cls, v: float, info: ValidationInfo) -> float:
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
        data = info.data
        if "direction" in data and "entry_price" in data:
            if data["direction"] == Direction.BUY and v <= data["entry_price"]:
                raise ValueError("BUY signal: take_profit_1 must be above entry_price")
            if data["direction"] == Direction.SELL and v >= data["entry_price"]:
                raise ValueError("SELL signal: take_profit_1 must be below entry_price")
        return v

    @property
    def risk_reward_ratio(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit_1 - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def risk_pips(self) -> float:
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
        from core.mt5_bridge import MT5Bridge
        from core.risk_manager import RiskManager

        self.mt5_bridge = MT5Bridge()
        self.risk_manager = RiskManager()
        self.bot_mode: str = os.getenv("BOT_MODE", "SIGNAL_ONLY")
        self._load_rules()
        self._start_time = datetime.utcnow()
        self._last_scan_at: Optional[datetime] = None
        self._signal_count_today: int = 0

        # Phase 5 components (lazy-loaded)
        self._consensus_engine = None
        self._regime_detector = None
        self._news_monitor = None

    def _get_consensus_engine(self):
        if self._consensus_engine is None:
            try:
                from core.consensus_engine import ConsensusEngine
                self._consensus_engine = ConsensusEngine()
            except Exception as exc:
                logger.warning(f"[SignalEngine] ConsensusEngine unavailable: {exc}")
        return self._consensus_engine

    def _get_regime_detector(self):
        if self._regime_detector is None:
            try:
                from core.market_regime import MarketRegimeDetector
                self._regime_detector = MarketRegimeDetector(self.mt5_bridge)
            except Exception as exc:
                logger.warning(f"[SignalEngine] MarketRegimeDetector unavailable: {exc}")
        return self._regime_detector

    def _get_news_monitor(self):
        if self._news_monitor is None:
            try:
                from core.news_monitor import NewsMonitor
                self._news_monitor = NewsMonitor()
            except Exception as exc:
                logger.warning(f"[SignalEngine] NewsMonitor unavailable: {exc}")
        return self._news_monitor

    def _load_rules(self) -> None:
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        with open(rules_path, encoding="utf-8") as f:
            self._rules = json.load(f)
        # Use explicit active_pairs list; fall back to flattening watchlist dict
        if "active_pairs" in self._rules:
            self._watchlist: list[str] = self._rules["active_pairs"]
        else:
            wl = self._rules.get("watchlist", {})
            if isinstance(wl, list):
                self._watchlist = wl
            else:
                self._watchlist = [p for cat in wl.values() for p in cat]

    async def _compute_indicators(self, pair: str, timeframe: str) -> dict:
        """Fetch recent OHLCV bars from MT5 and compute RSI, EMA50, EMA200, MACD."""
        try:
            import MetaTrader5 as mt5
            import asyncio as _asyncio

            TF_MAP = {
                "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
            }
            tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_M15)

            await _asyncio.to_thread(mt5.symbol_select, pair, True)
            rates = await _asyncio.to_thread(mt5.copy_rates_from_pos, pair, tf, 0, 250)
            if rates is None or len(rates) < 20:
                logger.warning(f"[SignalEngine] Not enough bars for {pair} {timeframe}")
                return {}

            closes = [r["close"] for r in rates]

            def _ema(prices: list[float], period: int) -> list[float]:
                k = 2.0 / (period + 1)
                result = [prices[0]]
                for p in prices[1:]:
                    result.append(p * k + result[-1] * (1 - k))
                return result

            def _rsi(prices: list[float], period: int = 14) -> float:
                if len(prices) < period + 1:
                    return 50.0
                gains, losses = [], []
                for i in range(1, period + 1):
                    diff = prices[-period - 1 + i] - prices[-period - 2 + i]
                    (gains if diff > 0 else losses).append(abs(diff))
                avg_gain = sum(gains) / period if gains else 0
                avg_loss = sum(losses) / period if losses else 1e-9
                rs = avg_gain / avg_loss
                return round(100 - (100 / (1 + rs)), 2)

            ema50_series = _ema(closes, 50)
            ema200_series = _ema(closes, 200)
            macd_fast = _ema(closes, 12)
            macd_slow = _ema(closes, 26)
            macd_line_series = [f - s for f, s in zip(macd_fast, macd_slow)]
            signal_series = _ema(macd_line_series, 9)

            ema50 = round(ema50_series[-1], 5)
            ema200 = round(ema200_series[-1], 5)
            macd_line = round(macd_line_series[-1], 5)
            signal_line = round(signal_series[-1], 5)
            histogram = round(macd_line - signal_line, 5)
            rsi = _rsi(closes)

            # Simple structure detection
            last_5 = closes[-5:]
            structure = "bullish" if last_5[-1] > last_5[0] else "bearish"

            # S/R: swing highs/lows in last 50 bars
            highs = [r["high"] for r in rates[-50:]]
            lows = [r["low"] for r in rates[-50:]]
            resistance = sorted(set([round(h, 2) for h in sorted(highs, reverse=True)[:3]]), reverse=True)
            support = sorted([round(l, 2) for l in sorted(lows)[:3]])

            # Recent 5 candles summary for Claude
            recent = []
            for r in rates[-5:]:
                recent.append({
                    "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"],
                    "vol": r["tick_volume"],
                })

            return {
                "rsi": rsi, "ema_50": ema50, "ema_200": ema200,
                "macd_line": macd_line, "signal_line": signal_line,
                "histogram": histogram, "structure": structure,
                "support_levels": support, "resistance_levels": resistance,
                "recent_candles": recent,
            }
        except Exception as exc:
            logger.warning(f"[SignalEngine] Indicator compute failed for {pair}: {exc}")
            return {}

    async def process_pair(self, pair: str, timeframe: str, strategy: str) -> dict:
        """Full signal pipeline for one trading pair.

        Phase 2: broadcasts WebSocket events at each key stage.
        Phase 5: news blackout check, regime-based strategy selection, consensus engine.
        """
        try:
            # Step 0 — Phase 5: News blackout check
            news_monitor = self._get_news_monitor()
            if news_monitor:
                try:
                    is_blackout, event_name = await news_monitor.is_news_blackout(pair)
                    if is_blackout:
                        logger.info(f"[SignalEngine] {pair} skipped — news blackout: {event_name}")
                        try:
                            from notifications.telegram import TelegramNotifier
                            await TelegramNotifier().send_news_blackout_alert(pair, event_name)
                        except Exception:
                            pass
                        return {"result": "SKIPPED", "pair": pair, "reason": f"News blackout: {event_name}"}
                except Exception as exc:
                    logger.warning(f"[SignalEngine] News check failed for {pair}: {exc}")

            # Step 0b — Phase 5: Auto strategy selection via Market Regime
            regime_info: Optional[dict] = None
            if strategy == "AUTO":
                regime_detector = self._get_regime_detector()
                if regime_detector:
                    try:
                        regime_info = await regime_detector.detect_regime(pair)
                        strategy = regime_info.get("recommended_strategy", "SWING")
                        if strategy == "AVOID":
                            logger.info(f"[SignalEngine] SKIPPED {pair}: High volatility regime")
                            return {"result": "SKIPPED", "pair": pair, "reason": "High volatility regime detected"}
                        logger.info(f"[SignalEngine] {pair} regime={regime_info['regime']}, strategy={strategy}")
                    except Exception as exc:
                        logger.warning(f"[SignalEngine] Regime detection failed for {pair}: {exc}")
                        strategy = "SWING"

            # Step 1 — Get current market data
            price_info = await self.mt5_bridge.get_current_price(pair)
            symbol_info = await self.mt5_bridge.get_symbol_info(pair)
            current_spread: int = int(symbol_info.get("spread", 0)) if symbol_info else 0

            # Step 1b — Fetch OHLCV candles and compute indicators
            indicator_data = await self._compute_indicators(pair, timeframe)

            # Step 2 — Package chart_data
            chart_data: dict = {
                "bid": price_info.get("bid", 0),
                "ask": price_info.get("ask", 0),
                "spread": current_spread,
                "price": price_info.get("ask", 0),
                "rsi": indicator_data.get("rsi"),
                "macd_line": indicator_data.get("macd_line"),
                "signal_line": indicator_data.get("signal_line"),
                "histogram": indicator_data.get("histogram"),
                "ema_50": indicator_data.get("ema_50"),
                "ema_200": indicator_data.get("ema_200"),
                "recent_candles": indicator_data.get("recent_candles", []),
                "structure": indicator_data.get("structure", "unknown"),
                "support_levels": indicator_data.get("support_levels", []),
                "resistance_levels": indicator_data.get("resistance_levels", []),
            }

            # Step 2b — Optionally inject news sentiment into chart_data
            if news_monitor:
                try:
                    sentiment = await news_monitor.fetch_market_sentiment(pair)
                    chart_data["news_sentiment"] = (
                        f"{sentiment.get('sentiment', 'NEUTRAL')} "
                        f"(score: {sentiment.get('score', 0):.2f}) — {sentiment.get('summary', '')}"
                    )
                except Exception:
                    pass

            # Step 3 — Phase 5: Analyse via ConsensusEngine (replaces direct ClaudeClient call)
            consensus_engine = self._get_consensus_engine()
            consensus_info: Optional[dict] = None
            if consensus_engine:
                result = await consensus_engine.get_consensus_signal(pair, timeframe, chart_data)
                # Also capture full consensus dict if CONSENSUS mode
                if os.getenv("CONSENSUS_MODE", "CLAUDE_ONLY") == "CONSENSUS":
                    consensus_info = await consensus_engine.analyze_with_consensus(pair, timeframe, chart_data)
            else:
                from core.claude_client import ClaudeClient
                result = await ClaudeClient().analyze_chart(pair, timeframe, chart_data)

            # Step 4 — Handle NO_TRADE
            if isinstance(result, NoTradeSignal):
                logger.info(f"NO_TRADE {pair}/{timeframe}: {result.reasoning}")
                return {"result": "NO_TRADE", "pair": pair, "reasoning": result.reasoning}

            signal: TradeSignal = result  # type: ignore[assignment]

            # Step 5 — Broadcast new signal to WebSocket clients
            await _ws_broadcast("new_signal", signal.model_dump(mode="json"))

            # Step 6 — Validate (RR, spread, confidence)
            valid, reason = self.risk_manager.validate_signal(signal, current_spread)
            if not valid:
                logger.warning(f"Signal REJECTED {pair}: {reason}")
                await _ws_broadcast("signal_rejected", {"pair": pair, "reason": reason})
                return {"result": "REJECTED", "pair": pair, "reason": reason}

            # Step 7 — Check position limits
            can_trade, pos_reason = await self.risk_manager.check_position_limits(
                pair, self.mt5_bridge
            )
            if not can_trade:
                logger.info(f"Position limit {pair}: {pos_reason}")
                return {"result": "SKIPPED", "pair": pair, "reason": pos_reason}

            # Step 8 — Calculate lot size (Phase 5: reduce if poor performer)
            account_info = await self.mt5_bridge.get_account_info()
            equity = account_info.get("equity", 10000.0)
            lot_size = self.risk_manager.calculate_lot_size(
                pair, signal.entry_price, signal.stop_loss, equity
            )
            try:
                from core.feedback_loop import FeedbackLoop
                fl = FeedbackLoop()
                should_reduce, multiplier = await fl.should_reduce_size_for_pair(pair)
                if should_reduce:
                    lot_size = round(lot_size * multiplier, 2)
                    logger.info(f"[FeedbackLoop] Reduced lot size for {pair} to {lot_size}")
            except Exception:
                pass

            # Step 9 — Persist signal to database
            try:
                from database.db import get_session
                from database import queries
                async with get_session() as session:
                    await queries.save_signal(session, signal)
                    self._signal_count_today += 1
            except Exception as db_err:
                logger.error(f"DB save_signal failed: {db_err}")

            # Step 10 — Execute or signal-only
            execution_result: Optional[dict] = None
            outcome = "SIGNAL"
            if self.bot_mode == "AUTO_EXECUTE":
                execution_result = await self.mt5_bridge.place_pending_order(signal, lot_size)
                if execution_result.get("success"):
                    outcome = "EXECUTED"
                    await _ws_broadcast("order_executed", {
                        "signal_id": signal.signal_id,
                        "pair": signal.pair,
                        "direction": signal.direction.value,
                        "order_id": execution_result.get("order_id"),
                    })
                    try:
                        from database.db import get_session
                        from database import queries
                        async with get_session() as session:
                            await queries.save_execution(
                                session, signal.signal_id, execution_result, lot_size
                            )
                    except Exception as db_err:
                        logger.error(f"DB save_execution failed: {db_err}")

            # Step 11 — Telegram notification (Phase 5: include regime + consensus info)
            try:
                from notifications.telegram import TelegramNotifier
                notifier = TelegramNotifier()
                await notifier.send_signal_alert(signal, lot_size, execution_result, self.bot_mode)
                # Voice alert (Phase 5)
                if outcome in ("SIGNAL", "EXECUTED"):
                    await notifier.send_voice_alert(signal)
            except Exception as notify_err:
                logger.warning(f"Telegram failed: {notify_err}")

            self._last_scan_at = datetime.utcnow()
            return {
                "result": outcome,
                "signal": signal.model_dump(),
                "lot_size": lot_size,
                "execution": execution_result,
                "regime": regime_info,
                "consensus": consensus_info,
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
        """Return current bot state dict."""
        uptime = (datetime.utcnow() - self._start_time).total_seconds()
        return {
            "mode": self.bot_mode,
            "state": "RUNNING",
            "uptime_seconds": round(uptime, 1),
            "daily_signals_count": self._signal_count_today,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
        }
