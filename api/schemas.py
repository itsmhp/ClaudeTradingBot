"""
api/schemas.py
==============
Pydantic v2 request/response models for all FastAPI endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "healthy"
    uptime_seconds: float
    mt5_connected: bool
    mcp_connected: bool
    bot_mode: str
    bot_state: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatusResponse(BaseModel):
    mode: str
    state: str
    active_positions: int
    pending_orders: int
    daily_pnl: float
    daily_pnl_percent: float
    daily_signals_count: int
    account_equity: float
    last_scan_at: Optional[str] = None
    next_scan_at: Optional[str] = None


class ExecuteRequest(BaseModel):
    pair: str = Field(..., examples=["XAUUSD"])
    timeframe: str = Field(..., examples=["H4"])
    strategy: Literal["SWING", "SCALPING"] = "SWING"


class PauseResumeResponse(BaseModel):
    success: bool
    message: str
    state: str


class PerformanceResponse(BaseModel):
    period: str
    total_signals: int
    executed_trades: int
    win_rate: float
    net_pnl: float
    profit_factor: float
    avg_rr_achieved: float
    max_drawdown_percent: float
    best_trade: Optional[dict[str, Any]] = None
    worst_trade: Optional[dict[str, Any]] = None


class SignalListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
