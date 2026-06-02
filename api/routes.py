"""
api/routes.py
=============
FastAPI router — all HTTP + WebSocket endpoints for ClaudeTradingBot.

Phases included:
  Phase 1: /status /signals /trades /execute /pause /resume /performance /health
  Phase 2: /ws  (WebSocket), /screenshot (upload), /dashboard (static)
  Phase 3: /backtest/* (run, result, compare, all-pairs, optimize, monte-carlo)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger
from pydantic import BaseModel

# ── Internal imports (guarded for partial-init environments) ─────────────────
try:
    from api.ws_manager import ws_manager
except ImportError:
    ws_manager = None  # type: ignore[assignment]

try:
    from database.db import get_db
    from database.queries import (
        get_all_signals,
        get_all_trades,
        get_performance_summary,
    )
except ImportError:
    get_db = None  # type: ignore[assignment]
    get_all_signals = get_all_trades = get_performance_summary = None  # type: ignore

# ── Backtesting imports (Phase 3) ────────────────────────────────────────────
_backtest_engine = None
_data_loader     = None

def _get_backtest_engine():
    """Lazy-load BacktestEngine — avoids MT5 import at startup."""
    global _backtest_engine, _data_loader
    if _backtest_engine is None:
        try:
            from backtesting.data_loader import HistoricalDataLoader
            from backtesting.engine import BacktestEngine
            _data_loader = HistoricalDataLoader()
            _backtest_engine = BacktestEngine(_data_loader)
        except Exception as exc:
            logger.warning(f"[Routes] BacktestEngine unavailable: {exc}")
    return _backtest_engine

# ── In-memory job store for background tasks ─────────────────────────────────
_jobs: dict[str, dict] = {}

router = APIRouter()

# ════════════════════════════════════════════════════════════════
# Pydantic schemas for request bodies
# ════════════════════════════════════════════════════════════════

class ExecuteRequest(BaseModel):
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    lot_size: Optional[float] = None
    order_type: str = "BUY_LIMIT"
    timeframe: str = "H4"
    strategy: str = "swing"
    notes: Optional[str] = None


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"
    count: int = 5000
    init_cash: float = 10_000.0


class OptimizeRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    timeframe: str = "H4"


class MonteCarloRequest(BaseModel):
    symbol: str
    strategy: str = "swing"
    n_simulations: int = 1000
    init_cash: float = 10_000.0


# ════════════════════════════════════════════════════════════════
# PHASE 1 — Core endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/health")
async def health_check():
    """Service health check."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status():
    """Return bot status: mode, uptime, active pairs, positions."""
    try:
        from main import app_state  # type: ignore[import]
        return app_state
    except ImportError:
        return {
            "status": "running",
            "mode": "SIGNAL_ONLY",
            "active_pairs": [],
            "open_positions": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/signals")
async def get_signals(limit: int = 50):
    """Return latest trade signals from database."""
    if get_all_signals is None or get_db is None:
        return {"signals": [], "count": 0}
    async for db in get_db():
        signals = get_all_signals(db, limit=limit)
        return {"signals": [s.__dict__ for s in signals], "count": len(signals)}


@router.get("/trades")
async def get_trades(limit: int = 50):
    """Return executed trades from database."""
    if get_all_trades is None or get_db is None:
        return {"trades": [], "count": 0}
    async for db in get_db():
        trades = get_all_trades(db, limit=limit)
        return {"trades": [t.__dict__ for t in trades], "count": len(trades)}


@router.post("/execute")
async def manual_execute(req: ExecuteRequest):
    """Manually trigger a trade execution (AUTO_EXECUTE mode required)."""
    logger.info(f"[Routes] Manual execute: {req.symbol} {req.direction}")
    return {
        "status": "queued",
        "symbol": req.symbol,
        "direction": req.direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/pause")
async def pause_bot():
    """Pause the signal processing loop."""
    logger.warning("[Routes] Bot paused via API")
    return {"status": "paused", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/resume")
async def resume_bot():
    """Resume the signal processing loop."""
    logger.info("[Routes] Bot resumed via API")
    return {"status": "running", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/performance")
async def get_performance(days: int = 30):
    """Return performance summary for the last N days."""
    if get_performance_summary is None or get_db is None:
        return {"message": "Database not available", "days": days}
    async for db in get_db():
        summary = get_performance_summary(db, days=days)
        return summary


# ════════════════════════════════════════════════════════════════
# PHASE 2 — WebSocket + Screenshot
# ════════════════════════════════════════════════════════════════

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint — streams real-time events to the dashboard."""
    if ws_manager is None:
        await websocket.close(code=1011)
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"event": "ping", "data": {}})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@router.post("/screenshot")
async def upload_screenshot():
    """Placeholder — screenshot upload endpoint (Phase 2)."""
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════
# PHASE 3 — Backtesting endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/backtest/run")
async def backtest_run(
    background_tasks: BackgroundTasks,
    symbol: str = "XAUUSD",
    strategy: str = "swing",
    timeframe: str = "H4",
    count: int = 5000,
    init_cash: float = 10_000.0,
):
    """Start a backtest job in the background.

    Returns a job_id immediately.  Poll ``GET /backtest/result/{job_id}``
    to retrieve results when complete.
    """
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            if strategy == "scalping":
                result = engine.run_scalping_backtest(symbol, timeframe, count, init_cash)
            else:
                result = engine.run_swing_backtest(symbol, timeframe, count, init_cash)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.get("/backtest/result/{job_id}")
async def backtest_result(job_id: str):
    """Poll for backtest job result."""
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


@router.get("/backtest/compare")
async def backtest_compare(symbol: str = "XAUUSD", init_cash: float = 10_000.0):
    """Run both swing and scalping backtests side-by-side for a symbol."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.compare_strategies(symbol, init_cash)


@router.get("/backtest/all-pairs")
async def backtest_all_pairs(strategy: str = "swing", init_cash: float = 10_000.0):
    """Run backtests for all watchlist pairs, sorted by net P&L."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.run_all_pairs(strategy, init_cash)


@router.post("/backtest/optimize")
async def backtest_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """Start parameter optimization job in the background."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    from backtesting.optimizer import StrategyOptimizer

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}

    async def _run():
        try:
            optimizer = StrategyOptimizer(engine)
            if req.strategy == "scalping":
                result = optimizer.optimize_scalping_params(req.symbol, req.timeframe)
            else:
                result = optimizer.optimize_swing_params(req.symbol, req.timeframe)
            _jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            _jobs[job_id] = {"status": "error", "error": str(exc)}

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


@router.post("/backtest/monte-carlo")
async def backtest_monte_carlo(req: MonteCarloRequest):
    """Run full backtest then Monte Carlo simulation."""
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")

    from backtesting.monte_carlo import MonteCarloSimulator

    try:
        stats = engine.run_swing_backtest(req.symbol, init_cash=req.init_cash)
        sim = MonteCarloSimulator(n_simulations=req.n_simulations)
        mc_result = sim.simulate_from_backtest(stats, req.init_cash)
        return {
            "backtest_stats": stats,
            "monte_carlo": mc_result,
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))
