"""Unit tests for risk manager."""

import pytest

from quantagent.common.models import Chain, CombinedSignal, SignalType
from quantagent.strategy.risk_manager import RiskManager


def _make_combined_signal(
    signal_type: SignalType = SignalType.BULLISH,
    confidence: float = 0.7,
    token: str = "SOL",
) -> CombinedSignal:
    """Helper to create a CombinedSignal for testing."""
    return CombinedSignal(
        token=token,
        chain=Chain.SOLANA,
        signal_type=signal_type,
        confidence=confidence,
    )


class TestRiskManager:
    def test_default_params(self):
        """RiskManager should initialize with sensible defaults."""
        rm = RiskManager()
        assert rm.max_position_size_usd == 1000.0
        assert rm.max_daily_loss_usd == 500.0
        assert rm.max_drawdown_pct == 0.15

    def test_low_risk_signal(self):
        """A strong signal with no risk factors should have low risk score."""
        rm = RiskManager()
        signal = _make_combined_signal(SignalType.BULLISH, 0.8)
        risk = rm.assess_risk(signal, portfolio_usd=10000, volatility_24h=0.03, liquidity_usd=500000)
        assert risk.risk_score < 0.3
        assert risk.max_position_usd > 0

    def test_emergency_stop_blocks_trading(self):
        """Emergency stop should block all trading with risk_score=1."""
        rm = RiskManager()
        rm.set_emergency_stop(True)
        signal = _make_combined_signal(SignalType.BULLISH, 0.9)
        risk = rm.assess_risk(signal)
        assert risk.risk_score == 1.0
        assert risk.max_position_usd == 0
        assert len(risk.warnings) > 0

    def test_high_volatility_increases_risk(self):
        """High volatility should increase risk score."""
        rm = RiskManager()
        signal = _make_combined_signal(SignalType.BULLISH, 0.7)
        low_vol = rm.assess_risk(signal, volatility_24h=0.03)
        high_vol = rm.assess_risk(signal, volatility_24h=0.20)
        assert high_vol.risk_score > low_vol.risk_score

    def test_low_confidence_increases_risk(self):
        """Low confidence signals should have higher risk."""
        rm = RiskManager()
        high_conf = _make_combined_signal(SignalType.BULLISH, 0.9)
        low_conf = _make_combined_signal(SignalType.BULLISH, 0.3)
        risk_high = rm.assess_risk(high_conf)
        risk_low = rm.assess_risk(low_conf)
        assert risk_low.risk_score > risk_high.risk_score

    def test_daily_loss_limit_warning(self):
        """Exceeding daily loss should generate a warning."""
        rm = RiskManager(max_daily_loss_usd=100)
        rm.record_pnl(-150)  # Exceeds daily loss
        signal = _make_combined_signal(SignalType.BULLISH, 0.7)
        risk = rm.assess_risk(signal)
        assert any("Daily loss" in w for w in risk.warnings)

    def test_daily_loss_auto_emergency_stop(self):
        """Exceeding 1.5x daily loss should trigger emergency stop."""
        rm = RiskManager(max_daily_loss_usd=100)
        rm.record_pnl(-160)  # 1.6x daily loss
        assert rm._emergency_stop is True

    def test_position_sizing_with_portfolio(self):
        """Position should be capped at 10% of portfolio."""
        rm = RiskManager(max_position_size_usd=1000)
        signal = _make_combined_signal(SignalType.BULLISH, 0.8)
        risk = rm.assess_risk(signal, portfolio_usd=5000)
        # 10% of 5000 = 500, which is less than max_position 1000
        assert risk.max_position_usd <= 500

    def test_position_sizing_without_portfolio(self):
        """Without portfolio info, should use max_position_size_usd."""
        rm = RiskManager(max_position_size_usd=1000)
        signal = _make_combined_signal(SignalType.BULLISH, 0.8)
        risk = rm.assess_risk(signal, portfolio_usd=0)
        assert risk.max_position_usd <= 1000

    def test_liquidity_risk(self):
        """Low liquidity should add to risk score."""
        rm = RiskManager(min_liquidity_usd=100000)
        signal = _make_combined_signal(SignalType.BULLISH, 0.7)
        good_liq = rm.assess_risk(signal, liquidity_usd=500000)
        bad_liq = rm.assess_risk(signal, liquidity_usd=10000)
        assert bad_liq.risk_score > good_liq.risk_score

    def test_record_pnl_updates_equity(self):
        """Recording P&L should update equity tracking."""
        rm = RiskManager()
        rm.record_pnl(100)
        rm.record_pnl(-50)
        assert rm._daily_pnl == 50
        assert rm._current_equity == 50

    def test_is_trading_allowed(self):
        """Trading should be allowed under normal conditions."""
        rm = RiskManager()
        assert rm.is_trading_allowed is True

    def test_is_trading_blocked_after_emergency(self):
        """Trading should be blocked after emergency stop."""
        rm = RiskManager()
        rm.set_emergency_stop(True)
        assert rm.is_trading_allowed is False

    def test_drawdown_warning(self):
        """Exceeding max drawdown should generate warning."""
        rm = RiskManager(max_drawdown_pct=0.10)
        rm.record_pnl(1000)  # Peak equity = 1000
        rm.record_pnl(-200)  # Drawdown = 20%
        signal = _make_combined_signal(SignalType.BULLISH, 0.7)
        risk = rm.assess_risk(signal)
        assert any("drawdown" in w.lower() for w in risk.warnings)
