"""
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

# Phase 5: Module-level cache for dashboard use
_regime_cache: dict = {}
_news_monitor_instance = None

# Global app state exposed to routes via `from main import app_state`
app_state: dict = {
    "status": "starting",
    "mode": os.getenv("BOT_MODE", "SIGNAL_ONLY"),
    "active_pairs": [],
    "open_positions": 0,
    "timestamp": None,
}


def _get_news_monitor():
    global _news_monitor_instance
    if _news_monitor_instance is None:
        try:
            from core.news_monitor import NewsMonitor
            _news_monitor_instance = NewsMonitor()
        except Exception:
            pass
    return _news_monitor_instance


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


async def _refresh_news_calendar() -> None:
    """Phase 5: keep the ForexFactory calendar cache warm."""
    nm = _get_news_monitor()
    if nm:
        try:
            events = await nm.fetch_economic_calendar(hours_ahead=24)
            logger.debug(f"[main] News calendar refreshed: {len(events)} events")
        except Exception as exc:
            logger.warning(f"[main] News calendar refresh failed: {exc}")


async def _refresh_regime_cache() -> None:
    """Phase 5: refresh market regime for all pairs every hour."""
    global _regime_cache
    try:
        from core.market_regime import MarketRegimeDetector
        detector = MarketRegimeDetector()
        _regime_cache = await detector.get_regime_for_all_pairs()
        logger.info(f"[main] Regime cache updated for {len(_regime_cache)} pairs")
    except Exception as exc:
        logger.warning(f"[main] Regime cache refresh failed: {exc}")


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
    # Phase 5: news calendar warm-up every 30 min, regime refresh every 60 min
    _scheduler.add_job(_refresh_news_calendar, IntervalTrigger(minutes=30),
                       id="news_calendar", replace_existing=True)
    _scheduler.add_job(_refresh_regime_cache, IntervalTrigger(minutes=60),
                       id="regime_cache", replace_existing=True)
    _scheduler.start()
    logger.info("Scheduler started. Dashboard: http://localhost:8000")

    # Update global state so /status endpoint reflects real values
    from datetime import datetime, timezone
    app_state["status"] = "running"
    app_state["mode"] = bot_mode
    app_state["timestamp"] = datetime.now(timezone.utc).isoformat()

    yield

    _scheduler.shutdown(wait=False)
    logger.info("ClaudeTradingBot stopped.")


app = FastAPI(
    title="ClaudeTradingBot API",
    version="5.0.0",
    description="AI trading signal system backed by Claude + MT5 (Exness) — Phase 5",
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
