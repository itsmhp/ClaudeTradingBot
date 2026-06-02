#!/usr/bin/env python3
"""
bootstrap_phase2.py
===================
Phase 2: Real-time dashboard, WebSocket support, screenshot feature.
Run AFTER bootstrap.py (Phase 1):

    python bootstrap.py        # Phase 1 — core modules
    python bootstrap_phase2.py # Phase 2 — dashboard + WS

Creates / overwrites:
  api/ws_manager.py          — WebSocket ConnectionManager
  api/routes.py              — updated with WS + screenshot endpoints
  core/signal_engine.py      — updated with ws_manager.broadcast() calls
  main.py                    — updated to serve dashboard as static files
  database/models.py         — updated with has_screenshot column
  dashboard/index.html       — complete single-file trading dashboard
  dashboard/static/screenshots/.gitkeep
"""
from pathlib import Path

BASE = Path(__file__).parent


def W(rel: str, content: str) -> None:
    """Write content to a file, creating parent directories."""
    p = BASE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"  OK  {rel}")


print("=" * 60)
print("ClaudeTradingBot — Phase 2 Bootstrap")
print("=" * 60)
print()

# ══════════════════════════════════════════════════════════════
# 1. api/ws_manager.py
# ══════════════════════════════════════════════════════════════
W("api/ws_manager.py", '''"""
api/ws_manager.py
=================
WebSocket connection manager — broadcasts real-time events
to all connected dashboard clients.

Import the singleton `ws_manager` in routes.py and signal_engine.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts JSON events."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WS client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket."""
        self.active_connections.discard(websocket)
        logger.info(f"WS client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, event: str, data: dict) -> None:
        """Send a JSON message to all connected clients.

        Message format:
            {"event": "<name>", "data": {...}, "timestamp": "<ISO>"}

        Stale connections are silently removed.
        """
        message = json.dumps({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        dead: set[WebSocket] = set()
        for ws in list(self.active_connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active_connections.discard(ws)


# Singleton — import this everywhere
ws_manager = ConnectionManager()
''')

# ══════════════════════════════════════════════════════════════
# 2. api/routes.py  (full updated — WS + screenshot)
# ══════════════════════════════════════════════════════════════
W("api/routes.py", '''"""
api/routes.py
=============
FastAPI APIRouter — all endpoints from MASTER_CONTEXT section 13,
plus WebSocket /ws/live and screenshot upload.
"""
from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    ExecuteRequest,
    HealthResponse,
    PauseResumeResponse,
    PerformanceResponse,
    SignalListResponse,
    StatusResponse,
)
from api.ws_manager import ws_manager
from database.db import get_session
from database import queries

router = APIRouter()

# Simple in-memory rate limiter for POST /execute
_execute_calls: list[float] = []
_BOT_STATE: dict[str, str] = {"state": "RUNNING"}
_START_TIME: float = time.time()

# PNG/JPEG magic bytes
_PNG_MAGIC = b"\\x89PNG"
_JPEG_MAGIC = b"\\xff\\xd8\\xff"
_MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MB


def _rate_limit_execute() -> None:
    """Allow max 10 calls to /execute per minute."""
    now = time.time()
    window = [t for t in _execute_calls if now - t < 60]
    if len(window) >= 10:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit: max 10 /execute requests per minute",
        )
    _execute_calls.clear()
    _execute_calls.extend(window)
    _execute_calls.append(now)


# ── WebSocket ─────────────────────────────────────────────────

@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    """Real-time event stream for dashboard clients.

    Events broadcast:
      new_signal        — TradeSignal generated
      order_executed    — MT5 order placed
      bot_state_change  — RUNNING / PAUSED
      daily_loss_limit  — loss limit breached
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep alive — accept any client message (ping/pong)
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── Health ────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — uptime, MT5 status, bot mode."""
    import os
    uptime = time.time() - _START_TIME
    return HealthResponse(
        status="healthy",
        uptime_seconds=round(uptime, 1),
        mt5_connected=True,
        mcp_connected=False,
        bot_mode=os.getenv("BOT_MODE", "SIGNAL_ONLY"),
        bot_state=_BOT_STATE["state"],
    )


# ── Status ────────────────────────────────────────────────────

@router.get("/status", response_model=StatusResponse)
async def bot_status() -> StatusResponse:
    """Current bot state — mode, positions, daily P&L."""
    import os
    return StatusResponse(
        mode=os.getenv("BOT_MODE", "SIGNAL_ONLY"),
        state=_BOT_STATE["state"],
        active_positions=0,
        pending_orders=0,
        daily_pnl=0.0,
        daily_pnl_percent=0.0,
        daily_signals_count=0,
        account_equity=0.0,
    )


# ── Signals ───────────────────────────────────────────────────

@router.get("/signals", response_model=SignalListResponse)
async def list_signals(
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> SignalListResponse:
    """List recent trade signals (paginated)."""
    records = await queries.get_recent_signals(session, limit=page_size)
    items = [
        {
            "signal_id": r.signal_id,
            "pair": r.pair,
            "direction": r.direction,
            "order_type": r.order_type,
            "entry_price": r.entry_price,
            "stop_loss": r.stop_loss,
            "take_profit_1": r.take_profit_1,
            "confidence": r.confidence,
            "strategy": r.strategy,
            "timeframe": r.timeframe,
            "reasoning": r.reasoning,
            "risk_reward_ratio": r.risk_reward_ratio,
            "was_executed": r.was_executed,
            "has_screenshot": getattr(r, "has_screenshot", False),
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]
    return SignalListResponse(items=items, total=len(items), page=page, page_size=page_size)


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a specific signal by signal_id."""
    from sqlalchemy import select
    from database.models import TradeSignalRecord
    stmt = select(TradeSignalRecord).where(TradeSignalRecord.signal_id == signal_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id!r} not found")
    return {
        "signal_id": record.signal_id,
        "pair": record.pair,
        "direction": record.direction,
        "entry_price": record.entry_price,
        "stop_loss": record.stop_loss,
        "take_profit_1": record.take_profit_1,
        "take_profit_2": record.take_profit_2,
        "confidence": record.confidence,
        "reasoning": record.reasoning,
        "has_screenshot": getattr(record, "has_screenshot", False),
        "created_at": record.created_at.isoformat(),
    }


# ── Screenshot upload ─────────────────────────────────────────

@router.post("/signals/{signal_id}/screenshot")
async def upload_screenshot(
    signal_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Upload a chart screenshot for a signal.

    Validates file type by magic bytes (not extension).
    Max 5 MB. Saves to dashboard/static/screenshots/{signal_id}.png.
    """
    from sqlalchemy import select
    from database.models import TradeSignalRecord

    # Verify signal exists
    stmt = select(TradeSignalRecord).where(TradeSignalRecord.signal_id == signal_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id!r} not found")

    # Read header bytes for magic-byte validation
    header = await file.read(8)
    is_png = header[:4] == b"\\x89PNG"
    is_jpg = header[:3] == b"\\xff\\xd8\\xff"
    if not (is_png or is_jpg):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only PNG and JPEG accepted (validated by file signature).",
        )

    # Read rest and check total size
    rest = await file.read()
    if len(header) + len(rest) > _MAX_SCREENSHOT_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Max 5 MB.")

    # Save file
    screenshots_dir = BASE / "dashboard" / "static" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    dest = screenshots_dir / f"{signal_id}.png"
    dest.write_bytes(header + rest)

    # Update database record
    record.has_screenshot = True  # type: ignore[attr-defined]
    await session.commit()

    return {"success": True, "path": f"/dashboard/static/screenshots/{signal_id}.png"}


# ── Trades ────────────────────────────────────────────────────

@router.get("/trades")
async def list_trades(
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List executed trades (paginated)."""
    from sqlalchemy import select
    from database.models import ExecutedTradeRecord
    stmt = (
        select(ExecutedTradeRecord)
        .order_by(ExecutedTradeRecord.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    result = await session.execute(stmt)
    records = result.scalars().all()
    items = [
        {
            "id": r.id,
            "signal_id": r.signal_id,
            "pair": r.pair,
            "direction": r.direction,
            "volume": r.volume,
            "entry_price": r.entry_price,
            "stop_loss": r.stop_loss,
            "take_profit_1": r.take_profit_1,
            "status": r.status,
            "profit": r.profit,
            "mt5_order_id": r.mt5_order_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]
    return {"items": items, "page": page, "page_size": page_size}


@router.get("/trades/{trade_id}")
async def get_trade(
    trade_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a specific executed trade by ID."""
    from sqlalchemy import select
    from database.models import ExecutedTradeRecord
    stmt = select(ExecutedTradeRecord).where(ExecutedTradeRecord.id == trade_id)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return {
        "id": record.id,
        "signal_id": record.signal_id,
        "pair": record.pair,
        "direction": record.direction,
        "volume": record.volume,
        "entry_price": record.entry_price,
        "stop_loss": record.stop_loss,
        "status": record.status,
        "profit": record.profit,
        "mt5_order_id": record.mt5_order_id,
        "created_at": record.created_at.isoformat(),
    }


# ── Execute ───────────────────────────────────────────────────

@router.post("/execute")
async def manual_execute(
    request: ExecuteRequest,
    _: None = Depends(_rate_limit_execute),
) -> dict[str, Any]:
    """Manually trigger a chart scan and signal generation."""
    if _BOT_STATE["state"] != "RUNNING":
        raise HTTPException(status_code=409, detail="Bot is not in RUNNING state")
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        result = await engine.process_pair(request.pair, request.timeframe, request.strategy)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Pause / Resume ────────────────────────────────────────────

@router.post("/pause", response_model=PauseResumeResponse)
async def pause_bot() -> PauseResumeResponse:
    """Pause the bot — stops new signal generation."""
    _BOT_STATE["state"] = "PAUSED"
    await ws_manager.broadcast("bot_state_change", {"state": "PAUSED"})
    return PauseResumeResponse(
        success=True,
        message="Bot paused. No new signals will be generated.",
        state="PAUSED",
    )


@router.post("/resume", response_model=PauseResumeResponse)
async def resume_bot() -> PauseResumeResponse:
    """Resume the bot after a pause."""
    _BOT_STATE["state"] = "RUNNING"
    await ws_manager.broadcast("bot_state_change", {"state": "RUNNING"})
    return PauseResumeResponse(
        success=True,
        message="Bot resumed. Signal generation active.",
        state="RUNNING",
    )


# ── Performance ───────────────────────────────────────────────

@router.get("/performance", response_model=PerformanceResponse)
async def performance(
    period: str = "today",
    session: AsyncSession = Depends(get_session),
) -> PerformanceResponse:
    """Performance summary for a given period."""
    perf = await queries.get_today_performance(session)
    return PerformanceResponse(
        period=period,
        total_signals=perf.get("count", 0),
        executed_trades=perf.get("count", 0),
        win_rate=perf.get("win_rate", 0.0),
        net_pnl=perf.get("net_pnl", 0.0),
        profit_factor=0.0,
        avg_rr_achieved=0.0,
        max_drawdown_percent=0.0,
        best_trade=None,
        worst_trade=None,
    )


@router.get("/performance/today")
async def performance_today(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Today\'s P&L breakdown from the database."""
    return await queries.get_today_performance(session)
''')

# ══════════════════════════════════════════════════════════════
# 3. core/signal_engine.py  (full updated — ws_manager broadcasts)
# ══════════════════════════════════════════════════════════════
W("core/signal_engine.py", '''"""
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
        from core.claude_client import ClaudeClient
        from core.risk_manager import RiskManager

        self.mt5_bridge = MT5Bridge()
        self.claude_client = ClaudeClient()
        self.risk_manager = RiskManager()
        self.bot_mode: str = os.getenv("BOT_MODE", "SIGNAL_ONLY")
        self._load_rules()
        self._start_time = datetime.utcnow()
        self._last_scan_at: Optional[datetime] = None
        self._signal_count_today: int = 0

    def _load_rules(self) -> None:
        rules_path = Path(__file__).parent.parent / "strategies" / "rules.json"
        with open(rules_path, encoding="utf-8") as f:
            self._rules = json.load(f)
        self._watchlist: list[str] = self._rules.get("watchlist", [])

    async def process_pair(self, pair: str, timeframe: str, strategy: str) -> dict:
        """Full signal pipeline for one trading pair.

        Phase 2: broadcasts WebSocket events at each key stage.
        """
        try:
            # Step 1 — Get current market data
            price_info = await self.mt5_bridge.get_current_price(pair)
            symbol_info = await self.mt5_bridge.get_symbol_info(pair)
            current_spread: int = int(symbol_info.get("spread", 0)) if symbol_info else 0

            # Step 2 — Package chart_data for Claude
            chart_data: dict = {
                "bid": price_info.get("bid", 0),
                "ask": price_info.get("ask", 0),
                "spread": current_spread,
                "price": price_info.get("ask", 0),
                "rsi": None,
                "macd_line": None,
                "signal_line": None,
                "histogram": None,
                "ema_50": None,
                "ema_200": None,
                "structure": "unknown",
                "support_levels": [],
                "resistance_levels": [],
            }

            # Step 3 — Analyse with Claude
            result = await self.claude_client.analyze_chart(pair, timeframe, chart_data)

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

            # Step 8 — Calculate lot size
            account_info = await self.mt5_bridge.get_account_info()
            equity = account_info.get("equity", 10000.0)
            lot_size = self.risk_manager.calculate_lot_size(
                pair, signal.entry_price, signal.stop_loss, equity
            )

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
                    # Broadcast execution event
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

            # Step 11 — Telegram notification
            try:
                from notifications.telegram import TelegramNotifier
                notifier = TelegramNotifier()
                await notifier.send_signal_alert(signal, lot_size, execution_result, self.bot_mode)
            except Exception as notify_err:
                logger.warning(f"Telegram failed: {notify_err}")

            self._last_scan_at = datetime.utcnow()
            return {
                "result": outcome,
                "signal": signal.model_dump(),
                "lot_size": lot_size,
                "execution": execution_result,
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
''')

# ══════════════════════════════════════════════════════════════
# 4. main.py  (full updated — static file serving)
# ══════════════════════════════════════════════════════════════
W("main.py", '''"""
main.py
=======
ClaudeTradingBot — application entry point.

Phase 2: serves the dashboard at http://localhost:8000/dashboard/
         GET / redirects to /dashboard/index.html

Usage
-----
    python main.py
    uvicorn main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

load_dotenv()

_scheduler = AsyncIOScheduler(timezone="UTC")


async def _run_swing_scan() -> None:
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        results = await engine.scan_all_pairs("SWING")
        logger.info(f"Swing scan complete: {len(results)} pairs processed")
    except Exception as exc:
        logger.error(f"Swing scan error: {exc}")


async def _run_scalping_scan() -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if not (7 <= now.hour < 21):
        return
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        results = await engine.scan_all_pairs("SCALPING")
        logger.info(f"Scalping scan complete: {len(results)} pairs processed")
    except Exception as exc:
        logger.error(f"Scalping scan error: {exc}")


async def _check_daily_loss() -> None:
    try:
        from core.mt5_bridge import MT5Bridge
        from core.risk_manager import RiskManager
        bridge = MT5Bridge()
        rm = RiskManager()
        breached, loss_pct = await rm.check_daily_loss(bridge)
        if breached:
            from api.ws_manager import ws_manager
            await ws_manager.broadcast("daily_loss_limit", {"loss_pct": loss_pct})
            from notifications.telegram import TelegramNotifier
            notifier = TelegramNotifier()
            await notifier.send_bot_paused(f"Daily loss limit reached: {loss_pct:.2f}%")
    except Exception as exc:
        logger.error(f"Daily loss check error: {exc}")


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("ClaudeTradingBot starting...")

    from database.db import init_db
    await init_db()

    bot_mode = os.getenv("BOT_MODE", "SIGNAL_ONLY")
    if bot_mode != "SIGNAL_ONLY":
        try:
            from core.mt5_bridge import MT5Bridge
            bridge = MT5Bridge()
            await bridge.connect()
            application.state.mt5_bridge = bridge
        except Exception as exc:
            logger.warning(f"MT5 connect skipped: {exc}")
    else:
        logger.info("SIGNAL_ONLY mode — MT5 connection skipped at startup")

    _scheduler.add_job(_run_swing_scan, CronTrigger(hour="1,5,9,13,17,21", minute=5),
                       id="swing_scan", replace_existing=True)
    _scheduler.add_job(_run_scalping_scan, IntervalTrigger(minutes=15),
                       id="scalping_scan", replace_existing=True)
    _scheduler.add_job(_check_daily_loss, IntervalTrigger(minutes=5),
                       id="loss_check", replace_existing=True)
    _scheduler.start()
    logger.info("Scheduler started. Dashboard: http://localhost:8000")

    yield

    _scheduler.shutdown(wait=False)
    logger.info("ClaudeTradingBot stopped.")


app = FastAPI(
    title="ClaudeTradingBot API",
    version="2.0.0",
    description="AI trading signal system backed by Claude + MT5 (Exness)",
    lifespan=lifespan,
)

# Mount dashboard static files
_dashboard_dir = Path("dashboard")
if _dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")
    logger.info("Dashboard mounted at /dashboard")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard/index.html")


from api.routes import router  # noqa: E402
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
''')

# ══════════════════════════════════════════════════════════════
# 5. database/models.py  (full updated — has_screenshot column)
# ══════════════════════════════════════════════════════════════
W("database/models.py", '''"""
database/models.py
==================
SQLAlchemy 2.0 ORM models for ClaudeTradingBot.
Phase 2: TradeSignalRecord gains has_screenshot column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeSignalRecord(Base):
    """Persists every signal generated by Claude AI."""

    __tablename__ = "trade_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_1: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    was_executed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_screenshot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )


class ExecutedTradeRecord(Base):
    """Records every MT5 order placed by the bot."""

    __tablename__ = "executed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trade_signals.signal_id"), nullable=False, index=True
    )
    mt5_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    mt5_ticket: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_1: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING", nullable=False)
    mt5_retcode: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mt5_comment: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class BotLogRecord(Base):
    """System event log."""

    __tablename__ = "bot_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )


class PerformanceSummaryRecord(Base):
    """Daily P&L and performance metrics."""

    __tablename__ = "performance_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    total_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    executed_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    starting_equity: Mapped[float] = mapped_column(Float, nullable=False)
    ending_equity: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now(), nullable=False
    )
''')

# ══════════════════════════════════════════════════════════════
# 6. dashboard placeholder files
# ══════════════════════════════════════════════════════════════
W("dashboard/static/screenshots/.gitkeep", "")

# ══════════════════════════════════════════════════════════════
# 7. dashboard/index.html  — complete single-file dashboard
# ══════════════════════════════════════════════════════════════
W("dashboard/index.html", """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ClaudeTradingBot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0d1117; --bg2: #161b22; --bg3: #1c2128; --bg4: #21262d;
  --border: #30363d; --border2: #484f58;
  --txt: #e6edf3; --txt2: #8b949e; --txt3: #484f58;
  --green: #00c853; --green2: #1b5e20; --red: #f44336; --red2: #4e0000;
  --amber: #ffc107; --blue: #2196f3; --orange: #ff9800; --purple: #9c27b0;
  --mono: 'JetBrains Mono', 'Courier New', monospace;
  --sans: 'Inter', system-ui, sans-serif;
  --sw: 220px; --hh: 56px; --r: 8px;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; font-family: var(--sans); background: var(--bg); color: var(--txt); font-size: 14px; }
a { color: var(--blue); text-decoration: none; }

/* === LAYOUT === */
#app { display: flex; flex-direction: column; height: 100vh; }
.app-body { display: flex; flex: 1; overflow: hidden; }
.main { flex: 1; overflow-y: auto; padding: 24px; }

/* === HEADER === */
header {
  height: var(--hh); background: var(--bg2); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; flex-shrink: 0; z-index: 100;
}
.h-left { display: flex; align-items: center; gap: 10px; }
.bot-icon { font-size: 20px; }
.bot-name { font-weight: 700; font-size: 16px; letter-spacing: -0.02em; }
.h-center { display: flex; align-items: center; gap: 10px; }
.h-right { font-size: 12px; color: var(--txt2); font-family: var(--mono); }

.ws-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--txt3);
  display: inline-block; transition: background 0.3s;
}
.ws-dot.connected { background: var(--green); box-shadow: 0 0 6px var(--green); }

.pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600;
  letter-spacing: 0.05em;
}
.pill-running { background: rgba(0,200,83,0.15); color: var(--green); border: 1px solid rgba(0,200,83,0.3); }
.pill-paused  { background: rgba(255,193,7,0.15); color: var(--amber); border: 1px solid rgba(255,193,7,0.3); }
.pill-error   { background: rgba(244,67,54,0.15); color: var(--red);   border: 1px solid rgba(244,67,54,0.3); }

.mode-badge {
  padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;
  letter-spacing: 0.04em;
}
.badge-signal { background: rgba(33,150,243,0.2); color: var(--blue); }
.badge-auto   { background: rgba(255,152,0,0.2); color: var(--orange); }

/* === SIDEBAR === */
.sidebar {
  width: var(--sw); background: var(--bg2); border-right: 1px solid var(--border);
  padding: 16px 0; overflow-y: auto; flex-shrink: 0;
}
.nav-list { list-style: none; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 11px 20px; cursor: pointer; color: var(--txt2);
  transition: all 0.15s; border-left: 3px solid transparent;
  user-select: none;
}
.nav-item:hover { color: var(--txt); background: rgba(255,255,255,0.04); }
.nav-item.active { color: var(--txt); background: rgba(33,150,243,0.08); border-left-color: var(--blue); }
.nav-icon { font-size: 16px; width: 20px; text-align: center; }

/* === SECTIONS === */
.section { display: none; }
.section.active { display: block; }
.section-title { font-size: 18px; font-weight: 600; margin-bottom: 20px; }

/* === STAT CARDS === */
.cards-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 24px; }
.card {
  background: var(--bg3); border: 1px solid var(--border); border-radius: var(--r); padding: 20px;
}
.card-label { font-size: 11px; color: var(--txt2); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px; }
.card-value { font-family: var(--mono); font-size: 22px; font-weight: 600; }
.card-sub { font-size: 11px; color: var(--txt2); margin-top: 4px; }
.positive { color: var(--green); }
.negative { color: var(--red); }
.neutral  { color: var(--txt2); }

/* === GRID ROWS === */
.row2 { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px; }
.row3 { margin-bottom: 24px; }

/* === CARD HEADER === */
.card-hdr { font-size: 13px; font-weight: 600; color: var(--txt2); text-transform: uppercase;
  letter-spacing: 0.06em; padding: 16px 20px 12px; border-bottom: 1px solid var(--border); }
.chart-wrap { padding: 16px; }

/* === TABLES === */
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 600;
  color: var(--txt2); text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border); white-space: nowrap; }
td { padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(255,255,255,0.02); }
.mono { font-family: var(--mono); font-size: 12px; }

/* === BADGES === */
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
}
.b-buy     { background: rgba(0,200,83,0.2); color: var(--green); }
.b-sell    { background: rgba(244,67,54,0.2); color: var(--red); }
.b-swing   { background: rgba(156,39,176,0.2); color: var(--purple); }
.b-scalp   { background: rgba(255,193,7,0.2); color: var(--amber); }
.b-exec    { background: rgba(0,200,83,0.15); color: var(--green); }
.b-signal  { background: rgba(33,150,243,0.15); color: var(--blue); }
.b-reject  { background: rgba(72,79,88,0.3); color: var(--txt2); }
.b-pending { background: rgba(255,193,7,0.15); color: var(--amber); }
.b-filled  { background: rgba(33,150,243,0.15); color: var(--blue); }
.b-win     { background: rgba(0,200,83,0.15); color: var(--green); }
.b-loss    { background: rgba(244,67,54,0.15); color: var(--red); }
.b-cancel  { background: rgba(72,79,88,0.3); color: var(--txt2); }

/* === SIGNAL CARDS (feed) === */
.sig-feed { padding: 0 4px; }
.sig-card {
  display: flex; gap: 14px; padding: 14px 16px;
  border-bottom: 1px solid var(--border); cursor: pointer;
  transition: background 0.1s; align-items: flex-start;
}
.sig-card:last-child { border-bottom: none; }
.sig-card:hover { background: rgba(255,255,255,0.02); }
.sig-card.flash { animation: flash 0.6s; }
@keyframes flash { 0%,100%{background:transparent} 50%{background:rgba(33,150,243,0.1)} }
.sig-bar { width: 4px; border-radius: 2px; flex-shrink: 0; margin-top: 2px; min-height: 50px; }
.sig-bar.buy  { background: var(--green); }
.sig-bar.sell { background: var(--red); }
.sig-body { flex: 1; }
.sig-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.sig-pair { font-weight: 700; font-size: 14px; }
.sig-prices { font-family: var(--mono); font-size: 12px; color: var(--txt2); margin-bottom: 6px; }
.sig-prices span { margin-right: 14px; }
.conf-bar-wrap { display: flex; align-items: center; gap: 8px; }
.conf-bar { flex: 1; height: 4px; background: var(--bg4); border-radius: 2px; max-width: 120px; }
.conf-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
.conf-pct { font-size: 11px; color: var(--txt2); font-family: var(--mono); }
.sig-time { font-size: 11px; color: var(--txt3); }
.sig-reasoning {
  display: none; margin-top: 10px; padding: 10px; background: var(--bg4);
  border-radius: 6px; font-size: 12px; color: var(--txt2); line-height: 1.6;
}
.sig-reasoning.visible { display: block; }
.sig-screenshot-area { margin-top: 8px; }
.screenshot-img { max-width: 100%; border-radius: 4px; border: 1px solid var(--border); margin-top: 6px; }
.btn-screenshot {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;
  background: var(--bg4); border: 1px solid var(--border); color: var(--txt2);
  cursor: pointer; transition: all 0.15s;
}
.btn-screenshot:hover { background: var(--bg2); color: var(--txt); }

/* === CONFIDENCE COLORS === */
.conf-high { background: var(--green); }
.conf-med  { background: var(--amber); }
.conf-low  { background: var(--txt3); }

/* === FILTER BAR === */
.filter-bar {
  display: flex; gap: 10px; padding: 16px; flex-wrap: wrap;
  border-bottom: 1px solid var(--border);
}
.filter-select {
  background: var(--bg4); border: 1px solid var(--border); color: var(--txt);
  border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer;
}
.filter-select:focus { outline: none; border-color: var(--blue); }

/* === PAGINATION === */
.pagination {
  display: flex; align-items: center; gap: 10px; padding: 14px 16px;
  border-top: 1px solid var(--border); font-size: 13px; color: var(--txt2);
}
.page-btn {
  padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg4); color: var(--txt); cursor: pointer; font-size: 13px;
}
.page-btn:hover { background: var(--bg2); }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* === PERFORMANCE TABS === */
.period-tabs { display: flex; gap: 4px; padding: 16px; border-bottom: 1px solid var(--border); }
.period-tab {
  padding: 6px 16px; border-radius: 6px; border: 1px solid transparent;
  background: transparent; color: var(--txt2); cursor: pointer; font-size: 13px;
  transition: all 0.15s;
}
.period-tab.active { background: rgba(33,150,243,0.15); color: var(--blue); border-color: rgba(33,150,243,0.3); }
.period-tab:hover:not(.active) { background: var(--bg4); color: var(--txt); }
.perf-charts { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; padding: 16px; }

/* === SETTINGS === */
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; }
.setting-item { padding: 14px 16px; background: var(--bg4); border-radius: var(--r); }
.setting-key { font-size: 11px; color: var(--txt2); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.setting-val { font-family: var(--mono); font-size: 14px; font-weight: 600; }
.btn-row { display: flex; gap: 10px; flex-wrap: wrap; padding: 16px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 9px 18px; border-radius: 6px; font-size: 13px; font-weight: 600;
  cursor: pointer; border: none; transition: all 0.15s;
}
.btn-pause   { background: rgba(255,193,7,0.2); color: var(--amber); border: 1px solid rgba(255,193,7,0.3); }
.btn-resume  { background: rgba(0,200,83,0.2); color: var(--green); border: 1px solid rgba(0,200,83,0.3); }
.btn-scan    { background: rgba(33,150,243,0.2); color: var(--blue); border: 1px solid rgba(33,150,243,0.3); }
.btn:hover { filter: brightness(1.2); }
.btn:active { transform: scale(0.97); }

/* === SKELETON === */
.sk { background: linear-gradient(90deg, var(--bg4) 25%, var(--bg2) 50%, var(--bg4) 75%);
  background-size: 200% 100%; animation: sk 1.5s infinite; border-radius: 4px; }
.sk-row { height: 14px; margin: 6px 0; }
.sk-val { height: 28px; width: 100px; }
@keyframes sk { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
.empty-msg { padding: 32px; text-align: center; color: var(--txt2); font-size: 13px; }

/* === TOAST === */
#toast-container { position: fixed; bottom: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; }
.toast {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; border-radius: 8px; font-size: 13px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.4); max-width: 380px;
  animation: slideIn 0.3s ease; border: 1px solid transparent;
}
.toast-success { background: rgba(0,200,83,0.15); border-color: rgba(0,200,83,0.3); color: var(--green); }
.toast-error   { background: rgba(244,67,54,0.15); border-color: rgba(244,67,54,0.3); color: var(--red); }
.toast-info    { background: rgba(33,150,243,0.15); border-color: rgba(33,150,243,0.3); color: var(--blue); }
.toast-warning { background: rgba(255,193,7,0.15); border-color: rgba(255,193,7,0.3); color: var(--amber); }
.toast.fade-out { animation: fadeOut 0.3s ease forwards; }
@keyframes slideIn  { from{transform:translateX(100%);opacity:0} to{transform:translateX(0);opacity:1} }
@keyframes fadeOut  { from{opacity:1} to{opacity:0;transform:translateX(100%)} }

/* === LOSS BANNER === */
#loss-banner {
  background: rgba(244,67,54,0.2); border-bottom: 2px solid var(--red);
  color: var(--red); padding: 10px 20px; text-align: center; font-weight: 600;
  font-size: 13px; display: none;
}
#loss-banner.visible { display: block; }

/* === MODAL === */
.modal-backdrop {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  z-index: 1000; align-items: center; justify-content: center;
}
.modal-backdrop.visible { display: flex; }
.modal-box {
  background: var(--bg2); border: 1px solid var(--border); border-radius: var(--r);
  padding: 24px; width: 420px; max-width: 95vw;
}
.modal-title { font-weight: 700; font-size: 16px; margin-bottom: 16px; }
.modal-body { margin-bottom: 20px; }
.form-row { margin-bottom: 14px; }
.form-label { display: block; font-size: 12px; color: var(--txt2); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em; }
.form-select, .form-input {
  width: 100%; background: var(--bg4); border: 1px solid var(--border);
  color: var(--txt); border-radius: 6px; padding: 9px 12px; font-size: 13px;
}
.form-select:focus, .form-input:focus { outline: none; border-color: var(--blue); }
.modal-footer { display: flex; gap: 10px; justify-content: flex-end; }
.btn-cancel { background: var(--bg4); color: var(--txt2); border: 1px solid var(--border); }
.btn-confirm { background: var(--blue); color: #fff; border: none; }

/* === SPINNER === */
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border);
  border-top-color: var(--blue); border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }

/* === RESPONSIVE === */
@media (max-width: 1200px) { .cards-grid { grid-template-columns: repeat(2,1fr); } }
@media (max-width: 900px)  { .row2 { grid-template-columns: 1fr; } .settings-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px)  { .sidebar { display: none; } .cards-grid { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<!-- Daily loss banner -->
<div id="loss-banner">&#9888; DAILY LOSS LIMIT REACHED &#8212; Bot Paused</div>

<div id="app">
<!-- HEADER -->
<header>
  <div class="h-left">
    <span class="bot-icon">&#9889;</span>
    <span class="bot-name">ClaudeTradingBot</span>
  </div>
  <div class="h-center">
    <span id="ws-dot" class="ws-dot" title="WebSocket: disconnected"></span>
    <span id="status-pill" class="pill pill-running">&#11044; RUNNING</span>
    <span id="mode-badge" class="mode-badge badge-signal">SIGNAL_ONLY</span>
  </div>
  <div class="h-right">
    <span id="last-refresh">&#8212;</span>
  </div>
</header>

<div class="app-body">
<!-- SIDEBAR -->
<nav class="sidebar">
  <ul class="nav-list">
    <li class="nav-item active" data-sec="dashboard" onclick="navigate('dashboard')">
      <span class="nav-icon">&#128202;</span> Dashboard
    </li>
    <li class="nav-item" data-sec="signals" onclick="navigate('signals')">
      <span class="nav-icon">&#128276;</span> Signals
    </li>
    <li class="nav-item" data-sec="trades" onclick="navigate('trades')">
      <span class="nav-icon">&#128188;</span> Trades
    </li>
    <li class="nav-item" data-sec="performance" onclick="navigate('performance')">
      <span class="nav-icon">&#128200;</span> Performance
    </li>
    <li class="nav-item" data-sec="settings" onclick="navigate('settings')">
      <span class="nav-icon">&#9881;</span> Settings
    </li>
  </ul>
</nav>

<!-- MAIN CONTENT -->
<main class="main">

<!-- ── SECTION: DASHBOARD ── -->
<section id="sec-dashboard" class="section active">
  <div class="cards-grid">
    <div class="card">
      <div class="card-label">Account Balance</div>
      <div class="card-value" id="val-balance"><span class="sk sk-val"></span></div>
    </div>
    <div class="card">
      <div class="card-label">Daily P&amp;L</div>
      <div class="card-value" id="val-pnl"><span class="sk sk-val"></span></div>
      <div class="card-sub" id="val-pnl-pct"></div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-value" id="val-winrate"><span class="sk sk-val"></span></div>
      <div class="card-sub" id="val-signals">&#8212; signals today</div>
    </div>
    <div class="card">
      <div class="card-label">Active Positions</div>
      <div class="card-value" id="val-positions"><span class="sk sk-val"></span></div>
    </div>
  </div>

  <div class="row2">
    <div class="card" style="padding:0">
      <div class="card-hdr">P&amp;L This Week</div>
      <div class="chart-wrap"><canvas id="pnl-chart"></canvas></div>
    </div>
    <div class="card" style="padding:0">
      <div class="card-hdr">Open Positions</div>
      <div id="positions-wrap" class="tbl-wrap">
        <div class="empty-msg">&#128684; No open positions</div>
      </div>
    </div>
  </div>

  <div class="card row3" style="padding:0">
    <div class="card-hdr">Recent Signals</div>
    <div id="recent-feed" class="sig-feed">
      <div class="empty-msg sk-row" style="width:60%;margin:20px auto"></div>
      <div class="empty-msg sk-row" style="width:40%;margin:10px auto"></div>
    </div>
  </div>
</section>

<!-- ── SECTION: SIGNALS ── -->
<section id="sec-signals" class="section">
  <div class="card" style="padding:0">
    <div class="filter-bar">
      <select class="filter-select" id="sig-pair" onchange="loadSignals()">
        <option value="">All Pairs</option>
        <option>XAUUSD</option><option>BTCUSD</option><option>EURUSD</option>
        <option>GBPUSD</option><option>USDJPY</option><option>NAS100</option><option>US30</option>
      </select>
      <select class="filter-select" id="sig-dir" onchange="loadSignals()">
        <option value="">All Directions</option>
        <option>BUY</option><option>SELL</option>
      </select>
      <select class="filter-select" id="sig-strat" onchange="loadSignals()">
        <option value="">All Strategies</option>
        <option>SCALPING</option><option>SWING</option>
      </select>
    </div>
    <div id="signals-body" class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Time</th><th>Pair</th><th>Dir</th><th>Type</th>
          <th>Entry</th><th>SL</th><th>TP1</th><th>R:R</th><th>Conf</th><th>Status</th>
        </tr></thead>
        <tbody id="signals-tbody"></tbody>
      </table>
    </div>
    <div class="pagination">
      <button class="page-btn" id="sig-prev" onclick="sigPage(-1)">&#8592; Prev</button>
      <span id="sig-pageinfo">Page 1</span>
      <button class="page-btn" id="sig-next" onclick="sigPage(1)">Next &#8594;</button>
    </div>
  </div>
  <!-- Expanded reasoning row (injected inline) -->
</section>

<!-- ── SECTION: TRADES ── -->
<section id="sec-trades" class="section">
  <div class="card" style="padding:0">
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Time</th><th>Pair</th><th>Dir</th><th>Lots</th>
          <th>Entry</th><th>SL</th><th>TP1</th><th>Status</th><th>P&amp;L</th>
        </tr></thead>
        <tbody id="trades-tbody"></tbody>
        <tfoot id="trades-tfoot"></tfoot>
      </table>
    </div>
    <div class="pagination">
      <button class="page-btn" id="trd-prev" onclick="trdPage(-1)">&#8592; Prev</button>
      <span id="trd-pageinfo">Page 1</span>
      <button class="page-btn" id="trd-next" onclick="trdPage(1)">Next &#8594;</button>
    </div>
  </div>
</section>

<!-- ── SECTION: PERFORMANCE ── -->
<section id="sec-performance" class="section">
  <div class="card" style="padding:0">
    <div class="period-tabs">
      <button class="period-tab active" data-period="today" onclick="setPeriod(this)">Today</button>
      <button class="period-tab" data-period="week" onclick="setPeriod(this)">This Week</button>
      <button class="period-tab" data-period="month" onclick="setPeriod(this)">This Month</button>
      <button class="period-tab" data-period="all" onclick="setPeriod(this)">All Time</button>
    </div>
    <div class="cards-grid" style="padding:16px">
      <div class="card">
        <div class="card-label">Total Signals</div>
        <div class="card-value" id="perf-signals">&#8212;</div>
      </div>
      <div class="card">
        <div class="card-label">Executed Trades</div>
        <div class="card-value" id="perf-trades">&#8212;</div>
      </div>
      <div class="card">
        <div class="card-label">Win Rate</div>
        <div class="card-value" id="perf-winrate">&#8212;</div>
      </div>
      <div class="card">
        <div class="card-label">Net P&amp;L</div>
        <div class="card-value" id="perf-pnl">&#8212;</div>
      </div>
    </div>
    <div class="perf-charts">
      <div><canvas id="equity-chart"></canvas></div>
      <div><canvas id="doughnut-chart"></canvas></div>
    </div>
  </div>
</section>

<!-- ── SECTION: SETTINGS ── -->
<section id="sec-settings" class="section">
  <div class="card" style="padding:0; margin-bottom:16px">
    <div class="card-hdr">Bot Configuration</div>
    <div class="settings-grid" id="settings-grid"></div>
  </div>
  <div class="card" style="padding:0">
    <div class="card-hdr">Bot Controls</div>
    <div class="btn-row">
      <button class="btn btn-pause"  onclick="pauseBot()">&#9208; Pause Bot</button>
      <button class="btn btn-resume" onclick="resumeBot()">&#9654; Resume Bot</button>
      <button class="btn btn-scan"   onclick="openScanModal()">&#9889; Manual Scan</button>
    </div>
  </div>
</section>

</main>
</div><!-- app-body -->
</div><!-- app -->

<!-- TOAST CONTAINER -->
<div id="toast-container"></div>

<!-- MODAL: Manual Scan -->
<div id="modal-scan" class="modal-backdrop" onclick="closeModal(event)">
  <div class="modal-box">
    <div class="modal-title">&#9889; Manual Scan</div>
    <div class="modal-body">
      <div class="form-row">
        <label class="form-label">Pair</label>
        <select id="scan-pair" class="form-select">
          <option>XAUUSD</option><option>BTCUSD</option><option>EURUSD</option>
          <option>GBPUSD</option><option>USDJPY</option><option>NAS100</option><option>US30</option>
        </select>
      </div>
      <div class="form-row">
        <label class="form-label">Timeframe</label>
        <select id="scan-tf" class="form-select">
          <option value="M15">M15 (Scalping)</option>
          <option value="H1">H1</option>
          <option value="H4" selected>H4 (Swing)</option>
          <option value="D1">D1</option>
        </select>
      </div>
      <div class="form-row">
        <label class="form-label">Strategy</label>
        <select id="scan-strategy" class="form-select">
          <option value="SWING">SWING</option>
          <option value="SCALPING">SCALPING</option>
        </select>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-cancel" onclick="closeScanModal()">Cancel</button>
      <button class="btn btn-confirm" id="scan-confirm-btn" onclick="executeScan()">&#9889; Scan</button>
    </div>
  </div>
</div>

<script>
// ================================================================
// CONFIG
// ================================================================
const API = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/ws/live';
const REFRESH_MS = 30000;

// ================================================================
// STATE
// ================================================================
let pnlChart = null, equityChart = null, doughnutChart = null;
let sigCurrentPage = 1, trdCurrentPage = 1;
let currentSection = 'dashboard';
let currentPeriod = 'today';
let ws = null, wsRetryDelay = 3000;
let pollTimer = null;

// ================================================================
// API HELPERS
// ================================================================
async function fetchAPI(path) {
  try {
    const r = await fetch(API + path);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch(e) {
    console.warn('fetchAPI error', path, e.message);
    return null;
  }
}

async function postAPI(path, body) {
  try {
    const r = await fetch(API + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'HTTP ' + r.status);
    return data;
  } catch(e) {
    showToast(e.message, 'error');
    return null;
  }
}

// ================================================================
// TOAST
// ================================================================
function showToast(msg, type, ms) {
  ms = ms || 4000;
  const icons = {success:'&#9989;', error:'&#10060;', info:'&#8505;&#65039;', warning:'&#9888;&#65039;'};
  const el = document.createElement('div');
  el.className = 'toast toast-' + (type || 'info');
  el.innerHTML = (icons[type] || icons.info) + ' <span>' + escHtml(msg) + '</span>';
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => { el.classList.add('fade-out'); setTimeout(() => el.remove(), 350); }, ms);
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ================================================================
// NAVIGATION
// ================================================================
function navigate(sec) {
  currentSection = sec;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('sec-' + sec).classList.add('active');
  document.querySelector('[data-sec="' + sec + '"]').classList.add('active');
  dispatch(sec);
}

function dispatch(sec) {
  if (sec === 'dashboard')   loadDashboard();
  if (sec === 'signals')     loadSignals();
  if (sec === 'trades')      loadTrades();
  if (sec === 'performance') loadPerformance();
  if (sec === 'settings')    loadSettings();
}

// ================================================================
// STATUS / HEADER
// ================================================================
function updateHeader(status) {
  if (!status) return;
  const pill = document.getElementById('status-pill');
  const state = (status.state || 'RUNNING').toUpperCase();
  pill.className = 'pill pill-' + state.toLowerCase();
  pill.innerHTML = '&#11044; ' + state;
  const badge = document.getElementById('mode-badge');
  const mode = (status.mode || 'SIGNAL_ONLY').toUpperCase();
  badge.textContent = mode;
  badge.className = 'mode-badge ' + (mode === 'AUTO_EXECUTE' ? 'badge-auto' : 'badge-signal');
  document.getElementById('last-refresh').textContent =
    'Refreshed ' + new Date().toLocaleTimeString();
}

// ================================================================
// DASHBOARD
// ================================================================
async function loadDashboard() {
  const [status, perf] = await Promise.all([
    fetchAPI('/status'),
    fetchAPI('/performance?period=today')
  ]);
  updateHeader(status);
  if (status) {
    const eq = status.account_equity || 0;
    document.getElementById('val-balance').textContent =
      '$' + eq.toLocaleString('en-US', {minimumFractionDigits: 2});
    const pnl = status.daily_pnl || 0;
    const pnlEl = document.getElementById('val-pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
    pnlEl.className = 'card-value ' + (pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '');
    document.getElementById('val-pnl-pct').textContent =
      (status.daily_pnl_percent || 0).toFixed(2) + '% today';
    document.getElementById('val-positions').textContent =
      (status.active_positions || 0) + ' / 5';
    document.getElementById('val-signals').textContent =
      (status.daily_signals_count || 0) + ' signals today';
  }
  if (perf) {
    const wr = perf.win_rate || 0;
    const wrEl = document.getElementById('val-winrate');
    wrEl.textContent = wr.toFixed(1) + '%';
    wrEl.className = 'card-value ' + (wr >= 60 ? 'positive' : wr >= 40 ? 'neutral' : 'negative');
  }
  await Promise.all([loadPnLChart(), loadPositions(), loadRecentSignals()]);
}

// ── P&L Chart ──
async function loadPnLChart() {
  const perf = await fetchAPI('/performance?period=week');
  const labels = getLast7Days();
  const values = labels.map(() => 0);
  if (perf && perf.net_pnl) values[6] = perf.net_pnl;

  const ctx = document.getElementById('pnl-chart').getContext('2d');
  if (pnlChart) pnlChart.destroy();
  const positive = perf && (perf.net_pnl || 0) >= 0;
  pnlChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: positive ? '#00c853' : '#f44336',
        backgroundColor: positive ? 'rgba(0,200,83,0.08)' : 'rgba(244,67,54,0.08)',
        fill: true, tension: 0.4, pointRadius: 3,
        pointBackgroundColor: positive ? '#00c853' : '#f44336'
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: {display:false}, tooltip: {
        callbacks: { label: ctx => ' $' + ctx.parsed.y.toFixed(2) }
      }},
      scales: {
        x: { ticks: {color:'#8b949e', font:{size:11}}, grid: {color:'#21262d'} },
        y: { ticks: {color:'#8b949e', font:{size:11}, callback: v => '$' + v},
             grid: {color:'#21262d'} }
      }
    }
  });
}

function getLast7Days() {
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    days.push(d.toLocaleDateString('en-US', {month:'short', day:'numeric'}));
  }
  return days;
}

// ── Open Positions ──
async function loadPositions() {
  const status = await fetchAPI('/status');
  const wrap = document.getElementById('positions-wrap');
  if (!status || status.active_positions === 0) {
    wrap.innerHTML = '<div class="empty-msg">&#128684; No open positions</div>';
    return;
  }
  wrap.innerHTML = '<div class="empty-msg">&#128202; ' + status.active_positions + ' position(s) open — data via MT5</div>';
}

// ── Recent Signals ──
async function loadRecentSignals() {
  const data = await fetchAPI('/signals?page=1&page_size=10');
  const feed = document.getElementById('recent-feed');
  if (!data || !data.items || data.items.length === 0) {
    feed.innerHTML = '<div class="empty-msg">&#128684; No signals yet</div>';
    return;
  }
  feed.innerHTML = data.items.map(s => buildSigCard(s)).join('');
}

function buildSigCard(s) {
  const dir = (s.direction || 'BUY').toUpperCase();
  const conf = s.confidence || 0;
  const confCls = conf >= 75 ? 'conf-high' : conf >= 60 ? 'conf-med' : 'conf-low';
  const statusBadge = s.was_executed ? '<span class="badge b-exec">EXECUTED</span>' :
                      '<span class="badge b-signal">SIGNAL</span>';
  const screenshotHtml = buildScreenshotArea(s);
  return '<div class="sig-card" onclick="toggleReasoning(this)">' +
    '<div class="sig-bar ' + dir.toLowerCase() + '"></div>' +
    '<div class="sig-body">' +
      '<div class="sig-top">' +
        '<span class="sig-pair">' + escHtml(s.pair) + '</span>' +
        '<span class="badge ' + (dir === 'BUY' ? 'b-buy' : 'b-sell') + '">' + dir + '</span>' +
        '<span class="badge ' + (s.strategy === 'SCALPING' ? 'b-scalp' : 'b-swing') + '">' +
          escHtml(s.strategy || '') + '</span>' +
        '<span class="badge" style="background:rgba(255,255,255,0.05);color:#8b949e">' +
          escHtml(s.timeframe || '') + '</span>' +
        statusBadge +
      '</div>' +
      '<div class="sig-prices">' +
        '<span>Entry <span class="mono">' + fmtPrice(s.entry_price) + '</span></span>' +
        '<span>SL <span class="mono negative">' + fmtPrice(s.stop_loss) + '</span></span>' +
        '<span>TP1 <span class="mono positive">' + fmtPrice(s.take_profit_1) + '</span></span>' +
        '<span>R:R <span class="mono">' + (s.risk_reward_ratio || '?') + '</span></span>' +
      '</div>' +
      '<div class="conf-bar-wrap">' +
        '<div class="conf-bar"><div class="conf-fill ' + confCls + '" style="width:' + conf + '%"></div></div>' +
        '<span class="conf-pct">' + conf + '%</span>' +
        '<span class="sig-time">' + timeAgo(s.created_at) + '</span>' +
      '</div>' +
      '<div class="sig-reasoning" data-signal-id="' + escHtml(s.signal_id || '') + '">' +
        escHtml(s.reasoning || '') +
        screenshotHtml +
      '</div>' +
    '</div>' +
  '</div>';
}

function buildScreenshotArea(s) {
  if (!s.signal_id) return '';
  if (s.has_screenshot) {
    return '<div class="sig-screenshot-area">' +
      '<img class="screenshot-img" src="/dashboard/static/screenshots/' +
      escHtml(s.signal_id) + '.png" alt="Chart screenshot">' +
      '</div>';
  }
  return '<div class="sig-screenshot-area">' +
    '<button class="btn-screenshot" onclick="openScreenshotPicker(event, \'' +
    escHtml(s.signal_id) + '\')">' +
    '&#128248; Add Screenshot</button>' +
    '<input type="file" class="ss-file-input" accept="image/png,image/jpeg" ' +
    'data-signal-id="' + escHtml(s.signal_id) + '" style="display:none" ' +
    'onchange="uploadScreenshot(event)">' +
    '</div>';
}

function toggleReasoning(card) {
  const r = card.querySelector('.sig-reasoning');
  if (r) r.classList.toggle('visible');
}

// ── Screenshot upload ──
function openScreenshotPicker(e, signalId) {
  e.stopPropagation();
  const input = e.target.closest('.sig-screenshot-area').querySelector('.ss-file-input');
  if (input) input.click();
}

async function uploadScreenshot(e) {
  const input = e.target;
  const signalId = input.dataset.signalId;
  const file = input.files[0];
  if (!file) return;
  const btn = input.previousElementSibling;
  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span> Uploading...';
  btn.disabled = true;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(API + '/signals/' + signalId + '/screenshot', {
      method: 'POST', body: fd
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Upload failed');
    const area = btn.closest('.sig-screenshot-area');
    area.innerHTML = '<img class="screenshot-img" src="' + data.path + '?' + Date.now() +
      '" alt="Chart screenshot">';
    showToast('Screenshot uploaded', 'success');
  } catch(err) {
    btn.innerHTML = orig;
    btn.disabled = false;
    showToast(err.message, 'error');
  }
}

// ================================================================
// SIGNALS SECTION
// ================================================================
async function loadSignals() {
  const pair = document.getElementById('sig-pair').value;
  const dir  = document.getElementById('sig-dir').value;
  const strat= document.getElementById('sig-strat').value;
  const data = await fetchAPI('/signals?page=' + sigCurrentPage + '&page_size=20');
  const tbody = document.getElementById('signals-tbody');
  if (!data || !data.items) { tbody.innerHTML = '<tr><td colspan="10" class="empty-msg">No data</td></tr>'; return; }

  let items = data.items;
  if (pair)  items = items.filter(s => s.pair === pair);
  if (dir)   items = items.filter(s => s.direction === dir);
  if (strat) items = items.filter(s => s.strategy === strat);

  tbody.innerHTML = items.length === 0 ?
    '<tr><td colspan="10" class="empty-msg">No signals match filters</td></tr>' :
    items.map(s => {
      const dir2 = s.direction || '';
      const executed = s.was_executed;
      return '<tr onclick="toggleSignalRow(this)" style="cursor:pointer">' +
        '<td class="mono" style="color:#8b949e">' + formatDt(s.created_at) + '</td>' +
        '<td><strong>' + escHtml(s.pair) + '</strong></td>' +
        '<td><span class="badge ' + (dir2==='BUY'?'b-buy':'b-sell') + '">' + escHtml(dir2) + '</span></td>' +
        '<td class="mono" style="font-size:11px">' + escHtml(s.order_type||'') + '</td>' +
        '<td class="mono">' + fmtPrice(s.entry_price) + '</td>' +
        '<td class="mono negative">' + fmtPrice(s.stop_loss) + '</td>' +
        '<td class="mono positive">' + fmtPrice(s.take_profit_1) + '</td>' +
        '<td class="mono">' + (s.risk_reward_ratio||'—') + '</td>' +
        '<td>' + confBadge(s.confidence) + '</td>' +
        '<td><span class="badge ' + (executed?'b-exec':'b-signal') + '">' +
          (executed?'EXECUTED':'SIGNAL') + '</span></td>' +
        '</tr>' +
        '<tr class="reasoning-row" style="display:none">' +
        '<td colspan="10" style="background:var(--bg4);padding:14px;color:var(--txt2);font-size:12px">' +
          escHtml(s.reasoning||'') + buildScreenshotArea(s) +
        '</td></tr>';
    }).join('');
  document.getElementById('sig-pageinfo').textContent = 'Page ' + sigCurrentPage;
  document.getElementById('sig-prev').disabled = sigCurrentPage <= 1;
}

function toggleSignalRow(tr) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains('reasoning-row')) {
    next.style.display = next.style.display === 'none' ? 'table-row' : 'none';
  }
}

function sigPage(d) { sigCurrentPage = Math.max(1, sigCurrentPage + d); loadSignals(); }

function confBadge(c) {
  c = c || 0;
  const cls = c >= 75 ? 'b-exec' : c >= 60 ? 'b-pending' : 'b-reject';
  return '<span class="badge ' + cls + '">' + c + '%</span>';
}

// ================================================================
// TRADES SECTION
// ================================================================
async function loadTrades() {
  const data = await fetchAPI('/trades?page=' + trdCurrentPage + '&page_size=20');
  const tbody = document.getElementById('trades-tbody');
  const tfoot = document.getElementById('trades-tfoot');
  if (!data || !data.items || data.items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-msg">No executed trades</td></tr>';
    tfoot.innerHTML = '';
    return;
  }
  tbody.innerHTML = data.items.map(t => {
    const pnl = t.profit || 0;
    const statusMap = {
      PENDING:'b-pending', FILLED:'b-filled', CLOSED_WIN:'b-win',
      CLOSED_LOSS:'b-loss', CANCELLED:'b-cancel'
    };
    return '<tr>' +
      '<td class="mono" style="color:#8b949e">' + formatDt(t.created_at) + '</td>' +
      '<td><strong>' + escHtml(t.pair||'') + '</strong></td>' +
      '<td><span class="badge ' + (t.direction==='BUY'?'b-buy':'b-sell') + '">' +
        escHtml(t.direction||'') + '</span></td>' +
      '<td class="mono">' + (t.volume||'—') + '</td>' +
      '<td class="mono">' + fmtPrice(t.entry_price) + '</td>' +
      '<td class="mono negative">' + fmtPrice(t.stop_loss) + '</td>' +
      '<td class="mono positive">' + fmtPrice(t.take_profit_1) + '</td>' +
      '<td><span class="badge ' + (statusMap[t.status]||'b-cancel') + '">' +
        escHtml(t.status||'') + '</span></td>' +
      '<td class="mono ' + (pnl>0?'positive':pnl<0?'negative':'') + '">' +
        (pnl>=0?'+':'') + '$' + pnl.toFixed(2) + '</td>' +
      '</tr>';
  }).join('');

  const totPnl = data.items.reduce((a,t) => a + (t.profit||0), 0);
  tfoot.innerHTML = '<tr style="border-top:2px solid var(--border)">' +
    '<td colspan="8" style="font-weight:600;padding:10px 14px">Total (' + data.items.length + ' trades)</td>' +
    '<td class="mono ' + (totPnl>=0?'positive':'negative') + '" style="font-weight:600;padding:10px 14px">' +
    (totPnl>=0?'+':'') + '$' + totPnl.toFixed(2) + '</td></tr>';
  document.getElementById('trd-pageinfo').textContent = 'Page ' + trdCurrentPage;
  document.getElementById('trd-prev').disabled = trdCurrentPage <= 1;
}

function trdPage(d) { trdCurrentPage = Math.max(1, trdCurrentPage + d); loadTrades(); }

// ================================================================
// PERFORMANCE SECTION
// ================================================================
async function loadPerformance() {
  const data = await fetchAPI('/performance?period=' + currentPeriod);
  if (!data) return;
  document.getElementById('perf-signals').textContent = data.total_signals || 0;
  document.getElementById('perf-trades').textContent  = data.executed_trades || 0;
  const wr = data.win_rate || 0;
  const wrEl = document.getElementById('perf-winrate');
  wrEl.textContent = wr.toFixed(1) + '%';
  wrEl.className = 'card-value ' + (wr >= 60 ? 'positive' : wr >= 40 ? 'neutral' : 'negative');
  const pnl = data.net_pnl || 0;
  const pnlEl = document.getElementById('perf-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
  pnlEl.className = 'card-value ' + (pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '');

  buildEquityChart([0, pnl]);
  buildDoughnutChart(data.win_rate || 50);
}

function setPeriod(btn) {
  document.querySelectorAll('.period-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  currentPeriod = btn.dataset.period;
  loadPerformance();
}

function buildEquityChart(data) {
  const ctx = document.getElementById('equity-chart').getContext('2d');
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: getLast7Days().slice(-data.length),
      datasets: [{
        data, borderColor: '#2196f3', backgroundColor: 'rgba(33,150,243,0.08)',
        fill: true, tension: 0.4, pointRadius: 3
      }]
    },
    options: {
      responsive: true, plugins: {legend:{display:false}},
      scales: {
        x: {ticks:{color:'#8b949e',font:{size:10}}, grid:{color:'#21262d'}},
        y: {ticks:{color:'#8b949e',font:{size:10},callback:v=>'$'+v}, grid:{color:'#21262d'}}
      }
    }
  });
}

function buildDoughnutChart(winRate) {
  const ctx = document.getElementById('doughnut-chart').getContext('2d');
  if (doughnutChart) doughnutChart.destroy();
  const loss = Math.max(0, 100 - winRate);
  doughnutChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Wins', 'Losses'],
      datasets: [{
        data: [winRate, loss],
        backgroundColor: ['rgba(0,200,83,0.8)', 'rgba(244,67,54,0.8)'],
        borderWidth: 0
      }]
    },
    options: {
      responsive: true, cutout: '65%',
      plugins: {
        legend: {position:'bottom', labels:{color:'#8b949e',font:{size:12}}},
        tooltip: {callbacks:{label: ctx => ctx.label+': '+ctx.parsed.toFixed(1)+'%'}}
      }
    }
  });
}

// ================================================================
// SETTINGS SECTION
// ================================================================
async function loadSettings() {
  const status = await fetchAPI('/status');
  const grid = document.getElementById('settings-grid');
  if (!status) { grid.innerHTML = '<div class="empty-msg">Could not load settings</div>'; return; }
  const items = [
    ['Bot Mode',      status.mode || '—'],
    ['Bot State',     status.state || '—'],
    ['Account Equity','$' + (status.account_equity || 0).toFixed(2)],
    ['Daily P&L',     '$' + (status.daily_pnl || 0).toFixed(2)],
    ['Active Positions', status.active_positions || 0],
    ['Daily Signals', status.daily_signals_count || 0],
    ['Last Scan',     status.last_scan_at ? formatDt(status.last_scan_at) : 'Never'],
    ['Next Scan',     status.next_scan_at  ? formatDt(status.next_scan_at)  : '—'],
  ];
  grid.innerHTML = items.map(([k,v]) =>
    '<div class="setting-item"><div class="setting-key">' + escHtml(k) + '</div>' +
    '<div class="setting-val">' + escHtml(String(v)) + '</div></div>'
  ).join('');
}

// ── Bot controls ──
async function pauseBot() {
  const r = await postAPI('/pause');
  if (r) { showToast('Bot paused', 'warning'); updateHeader({state:'PAUSED', mode: document.getElementById('mode-badge').textContent}); }
}
async function resumeBot() {
  const r = await postAPI('/resume');
  if (r) { showToast('Bot resumed', 'success'); updateHeader({state:'RUNNING', mode: document.getElementById('mode-badge').textContent}); }
}

// ── Manual scan modal ──
function openScanModal()  { document.getElementById('modal-scan').classList.add('visible'); }
function closeScanModal() { document.getElementById('modal-scan').classList.remove('visible'); }
function closeModal(e)    { if (e.target.classList.contains('modal-backdrop')) closeScanModal(); }

async function executeScan() {
  const pair     = document.getElementById('scan-pair').value;
  const tf       = document.getElementById('scan-tf').value;
  const strategy = document.getElementById('scan-strategy').value;
  const btn = document.getElementById('scan-confirm-btn');
  btn.innerHTML = '<span class="spinner"></span> Scanning...';
  btn.disabled = true;
  const r = await postAPI('/execute', {pair, timeframe: tf, strategy});
  btn.innerHTML = '&#9889; Scan';
  btn.disabled = false;
  closeScanModal();
  if (r) {
    const outcome = r.result || 'unknown';
    if (outcome === 'EXECUTED') showToast('Order executed: ' + pair + ' ' + (r.signal&&r.signal.direction||''), 'success');
    else if (outcome === 'SIGNAL') showToast('Signal: ' + pair + ' ' + (r.signal&&r.signal.direction||''), 'info');
    else if (outcome === 'NO_TRADE') showToast('No trade setup found for ' + pair, 'warning');
    else showToast('Scan result: ' + outcome, 'info');
  }
}

// ================================================================
// WEBSOCKET
// ================================================================
function connectWS() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    document.getElementById('ws-dot').className = 'ws-dot connected';
    document.getElementById('ws-dot').title = 'WebSocket: connected';
    wsRetryDelay = 3000;
    console.log('WS connected');
  };
  ws.onclose = ws.onerror = () => {
    document.getElementById('ws-dot').className = 'ws-dot';
    document.getElementById('ws-dot').title = 'WebSocket: disconnected';
    setTimeout(connectWS, wsRetryDelay);
    wsRetryDelay = Math.min(30000, wsRetryDelay * 2);
  };
  ws.onmessage = e => {
    try { handleWS(JSON.parse(e.data)); } catch(err) { console.warn('WS parse error', err); }
  };
}

function handleWS(msg) {
  const {event, data} = msg;
  if (event === 'new_signal') {
    showToast('&#128276; New Signal: ' + (data.pair||'') + ' ' + (data.direction||'') +
      ' (Conf: ' + (data.confidence||0) + '%)', 'info');
    if (currentSection === 'dashboard') prependSignalCard(data);
    const sc = document.getElementById('val-signals');
    if (sc) {
      const m = sc.textContent.match(/^(\\d+)/);
      if (m) sc.textContent = (parseInt(m[1])+1) + ' signals today';
    }
  }
  else if (event === 'order_executed') {
    showToast('&#9989; Order Placed: #' + (data.order_id||'?') + ' &#8212; ' +
      (data.pair||'') + ' ' + (data.direction||''), 'success');
    if (currentSection === 'dashboard') loadPositions();
  }
  else if (event === 'bot_state_change') {
    const state = data.state || 'RUNNING';
    updateHeader({state, mode: document.getElementById('mode-badge').textContent});
    showToast('Bot state: ' + state, state === 'RUNNING' ? 'success' : 'warning');
  }
  else if (event === 'daily_loss_limit') {
    document.getElementById('loss-banner').classList.add('visible');
    updateHeader({state:'PAUSED', mode: document.getElementById('mode-badge').textContent});
    showToast('&#9888; Daily loss limit reached!', 'error', 0);
  }
}

function prependSignalCard(signal) {
  const feed = document.getElementById('recent-feed');
  if (!feed) return;
  const div = document.createElement('div');
  div.innerHTML = buildSigCard(signal);
  const card = div.firstElementChild;
  if (!card) return;
  card.classList.add('flash');
  feed.insertBefore(card, feed.firstChild);
  const cards = feed.querySelectorAll('.sig-card');
  if (cards.length > 10) cards[cards.length-1].remove();
}

// ================================================================
// HELPERS
// ================================================================
function fmtPrice(v) {
  if (v == null) return '—';
  const n = parseFloat(v);
  return isNaN(n) ? '—' : n < 100 ? n.toFixed(5) : n.toFixed(2);
}

function formatDt(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric'}) + ' ' +
           d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false});
  } catch(e) { return iso; }
}

function timeAgo(iso) {
  if (!iso) return '';
  const secs = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (secs < 60) return secs + 's ago';
  if (secs < 3600) return Math.floor(secs/60) + 'm ago';
  if (secs < 86400) return Math.floor(secs/3600) + 'h ago';
  return Math.floor(secs/86400) + 'd ago';
}

// ================================================================
// AUTO-REFRESH POLLING (non-WS data)
// ================================================================
function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (currentSection === 'dashboard')   { loadStatCards_only(); }
    if (currentSection === 'performance') loadPerformance();
    if (currentSection === 'trades')      loadTrades();
  }, REFRESH_MS);
}

async function loadStatCards_only() {
  const [status, perf] = await Promise.all([
    fetchAPI('/status'), fetchAPI('/performance?period=today')
  ]);
  updateHeader(status);
  if (status) {
    const eq = status.account_equity || 0;
    const el = document.getElementById('val-balance');
    if (el) el.textContent = '$' + eq.toLocaleString('en-US',{minimumFractionDigits:2});
    const pnl = status.daily_pnl || 0;
    const pnlEl = document.getElementById('val-pnl');
    if (pnlEl) { pnlEl.textContent = (pnl>=0?'+':'')+'$'+pnl.toFixed(2);
      pnlEl.className = 'card-value '+(pnl>0?'positive':pnl<0?'negative':''); }
    const posEl = document.getElementById('val-positions');
    if (posEl) posEl.textContent = (status.active_positions||0) + ' / 5';
  }
  if (perf) {
    const wr = perf.win_rate || 0;
    const wrEl = document.getElementById('val-winrate');
    if (wrEl) { wrEl.textContent = wr.toFixed(1)+'%';
      wrEl.className='card-value '+(wr>=60?'positive':wr>=40?'neutral':'negative'); }
  }
}

// ================================================================
// INIT
// ================================================================
window.addEventListener('DOMContentLoaded', () => {
  connectWS();
  startPolling();
  loadDashboard();
});
</script>
</body>
</html>
""")

# ══════════════════════════════════════════════════════════════
# DONE
# ══════════════════════════════════════════════════════════════
print()
print("=" * 60)
print("Phase 2 bootstrap complete!")
print("=" * 60)
print()
print("Files created/updated:")
print("  api/ws_manager.py      — WebSocket ConnectionManager")
print("  api/routes.py          — WS endpoint + screenshot endpoint")
print("  core/signal_engine.py  — WS broadcast calls")
print("  main.py                — serves dashboard at /")
print("  database/models.py     — has_screenshot column added")
print("  dashboard/index.html   — complete trading dashboard")
print()
print("Access dashboard at: http://localhost:8000")
print("(after: pip install -r requirements.txt && python main.py)")
print()
