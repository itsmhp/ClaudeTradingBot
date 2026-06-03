"""
api/routes.py
=============
FastAPI router — all HTTP + WebSocket endpoints for ClaudeTradingBot.

Phases included:
  Phase 1: /status /signals /trades /execute /pause /resume /performance /health
  Phase 2: /ws  (WebSocket), /screenshot, /dashboard
  Phase 3: /backtest/* (run, result, compare, all-pairs, optimize, monte-carlo)
  Phase 4: /accounts/* (list, add, delete, status, aggregated, connect, disconnect, copy-performance)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger
from pydantic import BaseModel

# ── Internal imports ─────────────────────────────────────────────────────────
try:
    from api.ws_manager import ws_manager
except ImportError:
    ws_manager = None  # type: ignore[assignment]

try:
    from database.db import get_db
    from database.queries import get_all_signals, get_all_trades, get_performance_summary
except ImportError:
    get_db = None  # type: ignore[assignment]
    get_all_signals = get_all_trades = get_performance_summary = None  # type: ignore

# ── Phase 3: backtesting ─────────────────────────────────────────────────────
_backtest_engine = None
_data_loader     = None

def _get_backtest_engine():
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

# ── Phase 4: multi-account ───────────────────────────────────────────────────
_account_registry = None
_account_manager  = None
_copy_engine      = None

def _get_account_registry():
    global _account_registry
    if _account_registry is None:
        try:
            from multi_account.account_registry import AccountRegistry
            _account_registry = AccountRegistry(db_session=None)
        except Exception as exc:
            logger.warning(f"[Routes] AccountRegistry unavailable: {exc}")
    return _account_registry

def _get_account_manager():
    global _account_manager
    if _account_manager is None:
        reg = _get_account_registry()
        if reg:
            try:
                from multi_account.account_manager import AccountManager
                _account_manager = AccountManager(reg)
            except Exception as exc:
                logger.warning(f"[Routes] AccountManager unavailable: {exc}")
    return _account_manager

def _get_copy_engine():
    global _copy_engine
    if _copy_engine is None:
        reg = _get_account_registry()
        mgr = _get_account_manager()
        if reg and mgr:
            try:
                from multi_account.copy_engine import CopyEngine
                _copy_engine = CopyEngine(mgr, reg)
            except Exception as exc:
                logger.warning(f"[Routes] CopyEngine unavailable: {exc}")
    return _copy_engine

# ── In-memory job store ──────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ══════════════════════════════════════════════════════════════════════════════

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


class AddAccountRequest(BaseModel):
    account_id: str
    label: str
    login: int
    password: str
    server: str
    broker: str = "Exness"
    is_master: bool = False
    risk_per_trade_pct: float = 1.0
    lot_size_multiplier: float = 1.0
    copy_delay_seconds: int = 0
    max_positions: int = 5
    magic_number_offset: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# Auth dependency for sensitive account endpoints
# ══════════════════════════════════════════════════════════════════════════════

def require_bot_token(x_bot_token: Optional[str] = Header(default=None)):
    expected = os.getenv("BOT_API_TOKEN", "")
    if not expected:
        return  # No token configured — open access (dev mode)
    if x_bot_token != expected:
        raise HTTPException(403, "Invalid or missing X-Bot-Token header")


# ════════════════════════════════════════════════════════════════
# PHASE 1 — Core endpoints
# ════════════════════════════════════════════════════════════════

@router.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/status")
async def get_status():
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
    if get_all_signals is None or get_db is None:
        return {"signals": [], "count": 0}
    async for db in get_db():
        signals = get_all_signals(db, limit=limit)
        return {"signals": [s.__dict__ for s in signals], "count": len(signals)}


@router.get("/trades")
async def get_trades(limit: int = 50):
    if get_all_trades is None or get_db is None:
        return {"trades": [], "count": 0}
    async for db in get_db():
        trades = get_all_trades(db, limit=limit)
        return {"trades": [t.__dict__ for t in trades], "count": len(trades)}


@router.post("/execute")
async def manual_execute(req: ExecuteRequest):
    logger.info(f"[Routes] Manual execute: {req.symbol} {req.direction}")
    return {
        "status": "queued",
        "symbol": req.symbol,
        "direction": req.direction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }




class ScanRequest(BaseModel):
    symbol: str = "XAUUSD"
    strategy: str = "SWING"  # SWING, SCALPING, or AUTO
    timeframe: str = "H1"    # H1, H4, D1 for SWING; M5, M15 for SCALPING


@router.post("/scan")
async def manual_scan(req: ScanRequest):
    """Trigger a manual AI chart scan for a specific pair via Claude."""
    try:
        from core.signal_engine import SignalEngine
        engine = SignalEngine()
        signal = await engine.process_pair(req.symbol, req.timeframe, req.strategy)
        if signal:
            return {
                "status": "signal_found",
                "signal": signal.model_dump() if hasattr(signal, "model_dump") else signal,
            }
        return {"status": "no_signal", "pair": req.symbol, "strategy": req.strategy}
    except Exception as exc:
        logger.error(f"[scan] Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/pause")
async def pause_bot():
    logger.warning("[Routes] Bot paused via API")
    return {"status": "paused", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/resume")
async def resume_bot():
    logger.info("[Routes] Bot resumed via API")
    return {"status": "running", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/performance")
async def get_performance(days: int = 30):
    if get_performance_summary is None or get_db is None:
        return {"message": "Database not available", "days": days}
    async for db in get_db():
        return get_performance_summary(db, days=days)


# ════════════════════════════════════════════════════════════════
# PHASE 2 — WebSocket + Screenshot
# ════════════════════════════════════════════════════════════════

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════
# PHASE 3 — Backtesting
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
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


@router.get("/backtest/compare")
async def backtest_compare(symbol: str = "XAUUSD", init_cash: float = 10_000.0):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.compare_strategies(symbol, init_cash)


@router.get("/backtest/all-pairs")
async def backtest_all_pairs(strategy: str = "swing", init_cash: float = 10_000.0):
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    return engine.run_all_pairs(strategy, init_cash)


@router.post("/backtest/optimize")
async def backtest_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
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
    engine = _get_backtest_engine()
    if engine is None:
        raise HTTPException(503, "Backtesting engine not available")
    from backtesting.monte_carlo import MonteCarloSimulator
    try:
        stats = engine.run_swing_backtest(req.symbol, init_cash=req.init_cash)
        sim = MonteCarloSimulator(n_simulations=req.n_simulations)
        mc_result = sim.simulate_from_backtest(stats, req.init_cash)
        return {"backtest_stats": stats, "monte_carlo": mc_result}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ════════════════════════════════════════════════════════════════
# PHASE 4 — Multi-Account / Copy Trading
# ════════════════════════════════════════════════════════════════

@router.get("/accounts")
async def list_accounts():
    """List all MT5 accounts (passwords masked)."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    accounts = reg.list_accounts(mask_passwords=True)
    return {"accounts": [a.model_dump() for a in accounts], "count": len(accounts)}


@router.post("/accounts", dependencies=[Depends(require_bot_token)])
async def add_account(req: AddAccountRequest):
    """Add a new MT5 account.  Requires X-Bot-Token header."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    from multi_account.account_registry import MT5Account
    account = MT5Account(**req.model_dump())
    account_id = reg.add_account(account)
    return {"status": "added", "account_id": account_id}


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_bot_token)])
async def deactivate_account(account_id: str):
    """Deactivate an account (soft delete).  Requires X-Bot-Token header."""
    reg = _get_account_registry()
    if reg is None:
        raise HTTPException(503, "AccountRegistry not available")
    reg.deactivate_account(account_id)
    return {"status": "deactivated", "account_id": account_id}


@router.get("/accounts/aggregated")
async def accounts_aggregated():
    """Return aggregated equity, positions, and P&L across all accounts."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    return mgr.get_aggregated_status()


@router.get("/accounts/{account_id}/status")
async def account_status(account_id: str):
    """Return equity, positions, daily P&L for a single account."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    if not mgr.is_connected(account_id):
        raise HTTPException(404, f"Account {account_id} is not connected")
    try:
        bridge = mgr.get_bridge(account_id)
        info = bridge.get_account_info()
        return {"account_id": account_id, "info": info}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/accounts/{account_id}/connect")
async def connect_account(account_id: str):
    """Connect a specific account to MT5."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    success = mgr.connect_account(account_id)
    return {"account_id": account_id, "connected": success}


@router.post("/accounts/{account_id}/disconnect")
async def disconnect_account(account_id: str):
    """Disconnect a specific account from MT5."""
    mgr = _get_account_manager()
    if mgr is None:
        raise HTTPException(503, "AccountManager not available")
    mgr.disconnect_account(account_id)
    return {"account_id": account_id, "connected": False}


@router.get("/accounts/copy-performance")
async def copy_performance():
    """Return master vs follower copy trading performance comparison."""
    ce = _get_copy_engine()
    if ce is None:
        raise HTTPException(503, "CopyEngine not available")
    return ce.get_copy_performance()


# ════════════════════════════════════════════════════════════════
# PHASE 5 — Advanced AI: Regime, Sentiment, News, Consensus
# ════════════════════════════════════════════════════════════════

_consensus_engine_inst = None
_news_monitor_inst = None
_feedback_loop_inst = None


def _get_p5_consensus():
    global _consensus_engine_inst
    if _consensus_engine_inst is None:
        try:
            from core.consensus_engine import ConsensusEngine
            _consensus_engine_inst = ConsensusEngine()
        except Exception as exc:
            logger.warning(f"[Routes] ConsensusEngine unavailable: {exc}")
    return _consensus_engine_inst


def _get_p5_news_monitor():
    global _news_monitor_inst
    if _news_monitor_inst is None:
        try:
            from core.news_monitor import NewsMonitor
            _news_monitor_inst = NewsMonitor()
        except Exception as exc:
            logger.warning(f"[Routes] NewsMonitor unavailable: {exc}")
    return _news_monitor_inst


def _get_p5_feedback_loop():
    global _feedback_loop_inst
    if _feedback_loop_inst is None:
        try:
            from core.feedback_loop import FeedbackLoop
            _feedback_loop_inst = FeedbackLoop()
        except Exception as exc:
            logger.warning(f"[Routes] FeedbackLoop unavailable: {exc}")
    return _feedback_loop_inst


@router.get("/ai/regime")
async def ai_regime_all():
    """Return cached market regime for all pairs."""
    try:
        from main import _regime_cache  # type: ignore[import]
        if _regime_cache:
            return {"regimes": _regime_cache, "source": "cache"}
    except ImportError:
        pass
    # Compute on-demand if cache not available
    try:
        from core.market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()
        regimes = await detector.get_regime_for_all_pairs()
        return {"regimes": regimes, "source": "realtime"}
    except Exception as exc:
        raise HTTPException(503, f"Regime detection unavailable: {exc}")


@router.get("/ai/regime/{symbol}")
async def ai_regime_symbol(symbol: str):
    """Real-time regime detection for a specific symbol."""
    try:
        from core.market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()
        result = await detector.detect_regime(symbol.upper())
        return result
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/ai/sentiment/{symbol}")
async def ai_sentiment(symbol: str):
    """Fetch news sentiment for a symbol."""
    nm = _get_p5_news_monitor()
    if nm is None:
        raise HTTPException(503, "NewsMonitor not available")
    result = await nm.fetch_market_sentiment(symbol.upper())
    return result


@router.get("/ai/news-calendar")
async def ai_news_calendar(hours: int = 24):
    """Return upcoming high-impact news events."""
    nm = _get_p5_news_monitor()
    if nm is None:
        return {"events": [], "message": "NewsMonitor not available"}
    events = await nm.get_upcoming_events_formatted(hours_ahead=hours)
    return {"events": events, "count": len(events)}


@router.get("/ai/performance-context")
async def ai_performance_context(days: int = 30):
    """Return the FeedbackLoop performance summary string."""
    fl = _get_p5_feedback_loop()
    if fl is None:
        return {"context": "", "message": "FeedbackLoop not available"}
    context = await fl.build_performance_context(days=days)
    perf = await fl.get_recent_performance_by_pair(days=days)
    return {"context": context, "performance": perf, "days": days}


@router.get("/ai/consensus-stats")
async def ai_consensus_stats():
    """Return Claude vs GPT agreement rate stats."""
    ce = _get_p5_consensus()
    if ce is None:
        return {"message": "ConsensusEngine not available", "mode": "CLAUDE_ONLY"}
    return ce.get_consensus_stats()


# ════════════════════════════════════════════════════════════════
# DEBUG — MT5 diagnostics
# ════════════════════════════════════════════════════════════════

@router.get("/debug/mt5")
async def debug_mt5(keyword: str = "XAU"):
    """List all MT5 symbols containing the keyword and show tick for each."""
    import asyncio
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {"error": "MetaTrader5 not installed"}

    kw = keyword.upper()
    symbols = await asyncio.to_thread(mt5.symbols_get, f"*{kw}*")
    if not symbols:
        err = await asyncio.to_thread(mt5.last_error)
        return {"error": f"symbols_get failed: {err}", "keyword": kw}

    result = []
    for s in symbols[:20]:  # limit to 20
        await asyncio.to_thread(mt5.symbol_select, s.name, True)
        tick = await asyncio.to_thread(mt5.symbol_info_tick, s.name)
        result.append({
            "name": s.name,
            "bid": tick.bid if tick else None,
            "ask": tick.ask if tick else None,
        })
    return {"symbols": result, "count": len(symbols)}
