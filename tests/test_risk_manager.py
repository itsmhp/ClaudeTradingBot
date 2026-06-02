"""
tests/test_risk_manager.py
==========================
Unit tests for core/risk_manager.py (7 tests).
"""
import pytest

from core.risk_manager import RiskManager
from core.signal_engine import (
    Direction,
    NoTradeSignal,
    OrderType,
    Strategy,
    Timeframe,
    TradeSignal,
)


# ── Shared specs ──────────────────────────────────────────────

_XAUUSD = {"point": 0.01, "contract_size": 100.0, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}
_EURUSD = {"point": 0.00001, "contract_size": 100000.0, "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}


def _buy_signal(entry=2350.0, sl=2340.0, tp=2370.0, pair="XAUUSD", conf=78) -> TradeSignal:
    return TradeSignal(
        pair=pair,
        direction=Direction.BUY,
        order_type=OrderType.BUY_LIMIT,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        timeframe=Timeframe.H4,
        strategy=Strategy.SWING,
        confidence=conf,
        reasoning="Test reasoning string that is long enough to pass validation rules here.",
    )


# ── 1. XAUUSD lot size ────────────────────────────────────────

def test_lot_size_xauusd():
    """equity=10000, risk=1%, XAUUSD entry=2350, sl=2340 => 0.10 lots."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2340.0, 10000.0, _XAUUSD)
    assert lot == 0.10


# ── 2. EURUSD lot size ────────────────────────────────────────

def test_lot_size_eurusd():
    """equity=10000, risk=1%, EURUSD entry=1.08500, sl=1.08400 => 1.00 lot."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    lot = rm.calculate_lot_size("EURUSD", 1.08500, 1.08400, 10000.0, _EURUSD)
    assert lot == 1.00


# ── 3. Rounds to volume_step ──────────────────────────────────

def test_lot_size_rounds_to_step():
    """Result must be a multiple of volume_step (round down)."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    # entry=2350, sl=2343 => sl_dist=7 => raw=100/(700*1)=0.14285 => floor to 0.14
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2343.0, 10000.0, _XAUUSD)
    assert lot == 0.14
    # Verify it is a multiple of step (0.01)
    assert round(lot % 0.01, 8) == 0.0


# ── 4. Lot clamped to minimum ─────────────────────────────────

def test_lot_size_clamped_to_min():
    """Very small account => lot clamped to volume_min."""
    rm = RiskManager()
    rm.risk_pct = 1.0
    # equity=100 => risk=1 => raw=1/(1000*1)=0.001 => floor=0 => clamp to 0.01
    lot = rm.calculate_lot_size("XAUUSD", 2350.0, 2340.0, 100.0, _XAUUSD)
    assert lot == 0.01


# ── 5. validate_signal passes ─────────────────────────────────

def test_validate_signal_passes():
    """Valid signal with rr>=2.0 and spread within cap => (True, '')."""
    rm = RiskManager()
    rm.default_min_rr = 2.0
    sig = _buy_signal(entry=2350.0, sl=2340.0, tp=2370.0)
    valid, reason = rm.validate_signal(sig, current_spread=10)
    assert valid is True
    assert reason == ""


# ── 6. validate_signal fails low R:R ─────────────────────────

def test_validate_signal_fails_rr():
    """Signal with rr=1.5 must be rejected with 'R:R' in reason."""
    rm = RiskManager()
    rm.default_min_rr = 2.0
    # entry=2350, sl=2340 (risk=10), tp1=2365 (reward=15) => rr=1.5
    sig = _buy_signal(entry=2350.0, sl=2340.0, tp=2365.0)
    valid, reason = rm.validate_signal(sig, current_spread=10)
    assert valid is False
    assert "R:R" in reason or "r:r" in reason.lower()


# ── 7. validate_signal fails spread ──────────────────────────

def test_validate_signal_fails_spread():
    """Spread=50 for XAUUSD (cap=30) must be rejected with 'spread' in reason."""
    rm = RiskManager()
    sig = _buy_signal()
    valid, reason = rm.validate_signal(sig, current_spread=50)
    assert valid is False
    assert "spread" in reason.lower()
