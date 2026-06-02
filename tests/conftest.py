"""
tests/conftest.py
=================
Shared pytest fixtures for ClaudeTradingBot tests.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)
from database.models import Base


# ── mock_mt5 ─────────────────────────────────────────────────

@pytest.fixture
def mock_mt5(mocker):
    """Patch core.mt5_bridge.mt5 with a configured MagicMock."""
    mt5_mock = MagicMock()

    # Constants
    mt5_mock.TRADE_RETCODE_DONE = 10009
    mt5_mock.ORDER_TYPE_BUY_LIMIT = 2
    mt5_mock.ORDER_TYPE_SELL_LIMIT = 3
    mt5_mock.ORDER_TYPE_BUY_STOP = 4
    mt5_mock.ORDER_TYPE_SELL_STOP = 5
    mt5_mock.TRADE_ACTION_PENDING = 5
    mt5_mock.TRADE_ACTION_REMOVE = 8
    mt5_mock.ORDER_TIME_GTC = 1
    mt5_mock.ORDER_FILLING_IOC = 1

    # initialize / login
    mt5_mock.initialize.return_value = True
    mt5_mock.login.return_value = True
    mt5_mock.last_error.return_value = (0, "No error")
    mt5_mock.shutdown.return_value = True

    # account_info
    account = MagicMock()
    account.balance = 10000.0
    account.equity = 10000.0
    account.margin = 500.0
    account.margin_free = 9500.0
    account.currency = "USD"
    account.leverage = 500
    mt5_mock.account_info.return_value = account

    # symbol_info (XAUUSD specs by default)
    sym = MagicMock()
    sym.spread = 12
    sym.digits = 2
    sym.volume_min = 0.01
    sym.volume_max = 100.0
    sym.volume_step = 0.01
    sym.point = 0.01
    sym.trade_contract_size = 100.0
    mt5_mock.symbol_info.return_value = sym
    mt5_mock.symbol_select.return_value = True

    # symbol_info_tick
    tick = MagicMock()
    tick.bid = 2350.00
    tick.ask = 2350.12
    mt5_mock.symbol_info_tick.return_value = tick

    # positions / orders
    mt5_mock.positions_get.return_value = None
    mt5_mock.orders_get.return_value = None
    mt5_mock.history_deals_get.return_value = None

    # order_send
    order_result = MagicMock()
    order_result.retcode = 10009
    order_result.order = 12345
    mt5_mock.order_send.return_value = order_result

    mocker.patch("core.mt5_bridge.mt5", mt5_mock)
    return mt5_mock


# ── signal fixtures ───────────────────────────────────────────

@pytest.fixture
def sample_buy_signal() -> TradeSignal:
    """Valid XAUUSD BUY LIMIT signal."""
    return TradeSignal(
        pair="XAUUSD",
        direction=Direction.BUY,
        order_type=OrderType.BUY_LIMIT,
        entry_price=2350.0,
        stop_loss=2340.0,
        take_profit_1=2370.0,
        take_profit_2=2390.0,
        timeframe=Timeframe.H4,
        strategy=Strategy.SWING,
        confidence=78,
        reasoning=(
            "XAUUSD has pulled back to the 50 EMA on H4 after a clear break of "
            "structure above 2355. RSI at 52 confirms momentum is not exhausted."
        ),
        signal_id="test-signal-001",
    )


@pytest.fixture
def sample_sell_signal() -> TradeSignal:
    """Valid EURUSD SELL LIMIT signal."""
    return TradeSignal(
        pair="EURUSD",
        direction=Direction.SELL,
        order_type=OrderType.SELL_LIMIT,
        entry_price=1.08500,
        stop_loss=1.08650,
        take_profit_1=1.08200,
        timeframe=Timeframe.M5,
        strategy=Strategy.SCALPING,
        confidence=72,
        reasoning=(
            "EMA 9 crossed below EMA 21 on M5. Price approaching M15 resistance "
            "at 1.0850. RSI at 58, room to fall."
        ),
        signal_id="test-signal-002",
    )


# ── test_db ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_db() -> AsyncSession:
    """In-memory SQLite async session for database tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── mock_anthropic ────────────────────────────────────────────

@pytest.fixture
def mock_anthropic(mocker, sample_buy_signal):
    """Patch anthropic.Anthropic to return a mock response matching sample_buy_signal."""
    import json

    response_json = {
        "pair": "XAUUSD",
        "direction": "BUY",
        "order_type": "BUY_LIMIT",
        "entry_price": 2350.0,
        "stop_loss": 2340.0,
        "take_profit_1": 2370.0,
        "take_profit_2": 2390.0,
        "timeframe": "H4",
        "strategy": "SWING",
        "confidence": 78,
        "reasoning": (
            "XAUUSD has pulled back to the 50 EMA on H4 after a clear break of "
            "structure above 2355. RSI at 52 confirms momentum is not exhausted."
        ),
    }

    content_block = MagicMock()
    content_block.text = json.dumps(response_json)

    mock_response = MagicMock()
    mock_response.content = [content_block]

    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_response

    mock_anthropic_class = mocker.patch("anthropic.Anthropic", return_value=mock_client_instance)
    return mock_client_instance
