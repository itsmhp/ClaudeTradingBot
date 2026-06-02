"""
tests/test_mt5_bridge.py
========================
Unit tests for core/mt5_bridge.py (6 tests).
"""
import pytest
from core.mt5_bridge import MT5Bridge
from core.signal_engine import Direction, OrderType, Strategy, Timeframe, TradeSignal


# ── 1. Connect success ────────────────────────────────────────

async def test_connect_success(mock_mt5):
    """connect() returns True when initialize() and login() succeed."""
    bridge = MT5Bridge()
    result = await bridge.connect()
    assert result is True
    assert bridge._connected is True
    mock_mt5.initialize.assert_called_once()
    mock_mt5.login.assert_called_once()


# ── 2. Connect failure ────────────────────────────────────────

async def test_connect_failure(mock_mt5):
    """connect() raises ConnectionError when initialize() returns False."""
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = (0, "Terminal not found")
    bridge = MT5Bridge()
    with pytest.raises(ConnectionError):
        await bridge.connect()


# ── 3. Place BUY LIMIT order ──────────────────────────────────

async def test_place_buy_limit_order(mock_mt5, sample_buy_signal):
    """place_pending_order() builds correct request for a BUY_LIMIT signal."""
    bridge = MT5Bridge()
    await bridge.connect()
    result = await bridge.place_pending_order(sample_buy_signal, 0.10)

    assert result["success"] is True
    assert result["order_id"] == 12345

    call_kwargs = mock_mt5.order_send.call_args[0][0]
    assert call_kwargs["action"] == mock_mt5.TRADE_ACTION_PENDING
    assert call_kwargs["type"] == mock_mt5.ORDER_TYPE_BUY_LIMIT
    assert call_kwargs["magic"] == 234001  # XAUUSD magic number
    assert call_kwargs["symbol"] == "XAUUSD"
    assert call_kwargs["price"] == 2350.0


# ── 4. Retcode error ──────────────────────────────────────────

async def test_place_order_retcode_error(mock_mt5, sample_buy_signal):
    """place_pending_order() returns success=False for error retcodes."""
    order_result_mock = mock_mt5.order_send.return_value
    order_result_mock.retcode = 10006  # REJECT
    bridge = MT5Bridge()
    await bridge.connect()
    result = await bridge.place_pending_order(sample_buy_signal, 0.10)
    assert result["success"] is False
    assert result["retcode"] == 10006


# ── 5. Empty positions ────────────────────────────────────────

async def test_get_positions_empty(mock_mt5):
    """get_open_positions() returns [] when positions_get returns None."""
    mock_mt5.positions_get.return_value = None
    bridge = MT5Bridge()
    positions = await bridge.get_open_positions()
    assert positions == []


# ── 6. Spread check via risk_manager ─────────────────────────

async def test_spread_check(mock_mt5, sample_buy_signal):
    """validate_signal() returns False when spread exceeds the cap for XAUUSD."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    # XAUUSD cap is 30 points; pass spread=50
    valid, reason = rm.validate_signal(sample_buy_signal, current_spread=50)
    assert valid is False
    assert "spread" in reason.lower()
