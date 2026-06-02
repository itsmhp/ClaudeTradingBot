"""
tests/test_signal_engine.py
===========================
Unit tests for Pydantic models in core/signal_engine.py (6 tests).
"""
import pytest
from pydantic import ValidationError

from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)

_VALID_DICT = {
    "pair": "XAUUSD",
    "direction": "BUY",
    "order_type": "BUY_LIMIT",
    "entry_price": 2350.0,
    "stop_loss": 2340.0,
    "take_profit_1": 2370.0,
    "timeframe": "H4",
    "strategy": "SWING",
    "confidence": 78,
    "reasoning": "XAUUSD pulled back to 50 EMA on H4 after structure break. RSI at 52.",
}


# ── 1. Valid BUY signal parses ────────────────────────────────

def test_valid_buy_signal_parsed():
    """model_validate() succeeds on a well-formed BUY signal dict."""
    signal = TradeSignal.model_validate(_VALID_DICT)
    assert signal.pair == "XAUUSD"
    assert signal.direction == Direction.BUY
    assert signal.order_type == OrderType.BUY_LIMIT
    assert signal.entry_price == 2350.0


# ── 2. Invalid SL for BUY ─────────────────────────────────────

def test_invalid_sl_buy_rejected():
    """BUY with stop_loss >= entry_price must raise ValidationError."""
    bad = {**_VALID_DICT, "stop_loss": 2360.0}  # sl above entry
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 3. Invalid SL for SELL ────────────────────────────────────

def test_invalid_sl_sell_rejected():
    """SELL with stop_loss <= entry_price must raise ValidationError."""
    bad = {
        **_VALID_DICT,
        "direction": "SELL",
        "order_type": "SELL_LIMIT",
        "stop_loss": 2340.0,   # sl below entry — invalid for SELL
        "take_profit_1": 2320.0,
    }
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 4. Low confidence rejected ────────────────────────────────

def test_low_confidence_rejected():
    """confidence < 60 must raise ValidationError."""
    bad = {**_VALID_DICT, "confidence": 45}
    with pytest.raises(ValidationError):
        TradeSignal.model_validate(bad)


# ── 5. R:R ratio calculated correctly ────────────────────────

def test_rr_ratio_calculated():
    """risk_reward_ratio property: entry=2350, sl=2340, tp1=2370 => 2.0."""
    signal = TradeSignal.model_validate(_VALID_DICT)
    assert signal.risk_reward_ratio == 2.0


# ── 6. NoTradeSignal parses ───────────────────────────────────

def test_no_trade_signal_parsed():
    """NoTradeSignal with signal=NO_TRADE and reasoning parses correctly."""
    data = {"signal": "NO_TRADE", "reasoning": "No valid setup found on this timeframe."}
    s = NoTradeSignal.model_validate(data)
    assert s.signal == "NO_TRADE"
    assert len(s.reasoning) >= 10
