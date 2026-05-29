"""Unit tests for momentum strategy."""

import pytest

from quantagent.common.models import (
    ActionType,
    Chain,
    CombinedSignal,
    RiskAssessment,
    SignalType,
)
from quantagent.strategy.momentum import MomentumStrategy


def _make_signal(
    signal_type: SignalType = SignalType.BULLISH,
    confidence: float = 0.7,
    token: str = "SOL",
) -> CombinedSignal:
    return CombinedSignal(
        token=token, chain=Chain.SOLANA, signal_type=signal_type, confidence=confidence,
    )


def _make_risk(
    risk_score: float = 0.3,
    max_position_usd: float = 500.0,
    suggested_stop_loss_pct: float = 0.05,
    liquidity_score: float = 0.8,
) -> RiskAssessment:
    return RiskAssessment(
        risk_score=risk_score,
        max_position_usd=max_position_usd,
        suggested_stop_loss_pct=suggested_stop_loss_pct,
        liquidity_score=liquidity_score,
    )


class TestMomentumStrategy:
    def test_bullish_signal_triggers_buy(self):
        """Strong bullish signal should produce a BUY action."""
        strategy = MomentumStrategy(min_confidence=0.5)
        signal = _make_signal(SignalType.BULLISH, 0.7)
        risk = _make_risk()
        action = strategy.evaluate(signal, risk)
        assert action.action == ActionType.BUY

    def test_bearish_signal_triggers_hold(self):
        """Bearish signal should produce HOLD (no shorting in this strategy)."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.BEARISH, 0.7)
        risk = _make_risk()
        action = strategy.evaluate(signal, risk)
        assert action.action == ActionType.HOLD

    def test_neutral_signal_triggers_hold(self):
        """Neutral signal should produce HOLD."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.NEUTRAL, 0.5)
        risk = _make_risk()
        action = strategy.evaluate(signal, risk)
        assert action.action == ActionType.HOLD

    def test_low_confidence_triggers_hold(self):
        """Signal below min_confidence should produce HOLD."""
        strategy = MomentumStrategy(min_confidence=0.6)
        signal = _make_signal(SignalType.BULLISH, 0.4)
        risk = _make_risk()
        action = strategy.evaluate(signal, risk)
        assert action.action == ActionType.HOLD

    def test_high_risk_triggers_hold(self):
        """Very high risk score should produce HOLD."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.BULLISH, 0.8)
        risk = _make_risk(risk_score=0.8)
        action = strategy.evaluate(signal, risk)
        assert action.action == ActionType.HOLD

    def test_position_size_scales_with_confidence(self):
        """Higher confidence should produce larger position size."""
        strategy = MomentumStrategy(max_position_pct=0.1)
        low_conf_signal = _make_signal(SignalType.BULLISH, 0.6)
        high_conf_signal = _make_signal(SignalType.BULLISH, 0.9)
        risk = _make_risk()
        action_low = strategy.evaluate(low_conf_signal, risk)
        action_high = strategy.evaluate(high_conf_signal, risk)
        assert action_high.position_size_pct >= action_low.position_size_pct

    def test_stop_loss_set_when_liquidity_ok(self):
        """Stop loss should be set when liquidity is adequate."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.BULLISH, 0.7)
        risk = _make_risk(liquidity_score=0.8)
        action = strategy.evaluate(signal, risk)
        assert action.stop_loss is not None
        assert action.stop_loss < 1.0  # e.g., 0.95 means 5% stop loss

    def test_stop_loss_none_when_no_liquidity(self):
        """Stop loss should be None when there's no liquidity."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.BULLISH, 0.7)
        risk = _make_risk(liquidity_score=0.0)
        action = strategy.evaluate(signal, risk)
        assert action.stop_loss is None

    def test_take_profit_risk_reward_ratio(self):
        """Take profit should be reward_risk_ratio * stop_loss_pct."""
        strategy = MomentumStrategy(reward_risk_ratio=2.0)
        signal = _make_signal(SignalType.BULLISH, 0.7)
        risk = _make_risk(suggested_stop_loss_pct=0.05, liquidity_score=0.8)
        action = strategy.evaluate(signal, risk)
        if action.stop_loss is not None and action.take_profit is not None:
            # Stop loss distance from 1.0
            sl_distance = 1.0 - action.stop_loss
            tp_distance = action.take_profit - 1.0
            # tp_distance should be ~2x sl_distance (2:1 R:R)
            assert abs(tp_distance / sl_distance - 2.0) < 0.1

    def test_reasoning_includes_confidence(self):
        """Reasoning should include confidence information."""
        strategy = MomentumStrategy()
        signal = _make_signal(SignalType.BULLISH, 0.65)
        risk = _make_risk()
        action = strategy.evaluate(signal, risk)
        assert "0.65" in action.reasoning or "conf" in action.reasoning.lower()

    def test_custom_params(self):
        """Strategy should accept custom parameters."""
        strategy = MomentumStrategy(
            min_confidence=0.8,
            atr_stop_multiplier=2.0,
            reward_risk_ratio=3.0,
            max_position_pct=0.05,
        )
        assert strategy.min_confidence == 0.8
        assert strategy.reward_risk_ratio == 3.0
