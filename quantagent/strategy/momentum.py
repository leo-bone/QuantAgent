"""Momentum strategy - follow the trend.

Core idea: When multiple signals agree on a direction with high confidence,
ride the momentum. Uses ATR-based stop losses and take profits.
"""

from __future__ import annotations

from quantagent.common.models import (
    ActionType,
    CombinedSignal,
    RiskAssessment,
    SignalType,
    StrategyAction,
)
from quantagent.strategy.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """Momentum-following strategy.

    - Enters on strong directional signals (confidence > threshold)
    - Uses ATR-based stop loss (1.5x ATR below entry for longs)
    - Take profit at 2:1 reward/risk ratio
    - Position size scales with confidence
    """

    name = "momentum"
    description = "Follow strong directional signals with ATR-based risk management"

    def __init__(
        self,
        min_confidence: float = 0.55,
        atr_stop_multiplier: float = 1.5,
        reward_risk_ratio: float = 2.0,
        max_position_pct: float = 0.1,
    ):
        self.min_confidence = min_confidence
        self.atr_stop_multiplier = atr_stop_multiplier
        self.reward_risk_ratio = reward_risk_ratio
        self.max_position_pct = max_position_pct

    def evaluate(
        self,
        signal: CombinedSignal,
        risk: RiskAssessment,
        portfolio_state: dict | None = None,
    ) -> StrategyAction:
        """Evaluate signal and produce momentum action."""
        # Determine action from signal direction
        if signal.signal_type == SignalType.BULLISH:
            action = ActionType.BUY
        elif signal.signal_type == SignalType.BEARISH:
            # For now, we only go long in crypto (shorting is complex)
            # In production, this could trigger a close/deleverage
            action = ActionType.HOLD
        else:
            return StrategyAction(
                action=ActionType.HOLD,
                token=signal.token,
                chain=signal.chain,
                reasoning="Signal is neutral, no momentum to follow",
            )

        # Check confidence threshold
        if signal.confidence < self.min_confidence:
            return StrategyAction(
                action=ActionType.HOLD,
                token=signal.token,
                chain=signal.chain,
                reasoning=f"Confidence {signal.confidence:.2f} below threshold {self.min_confidence}",
            )

        # Check risk
        if risk.risk_score > 0.7:
            return StrategyAction(
                action=ActionType.HOLD,
                token=signal.token,
                chain=signal.chain,
                reasoning=f"Risk score {risk.risk_score:.2f} too high",
            )

        # Calculate position size (scales with confidence)
        position_pct = min(
            self.max_position_pct * (signal.confidence / 1.0),
            self.max_position_pct,
        )

        # Calculate stop loss and take profit
        stop_loss_pct = risk.suggested_stop_loss_pct if risk.suggested_stop_loss_pct > 0 else 0.05
        take_profit_pct = stop_loss_pct * self.reward_risk_ratio

        reasoning_parts = [
            f"Momentum signal: {signal.signal_type.value} (conf: {signal.confidence:.2f})",
            f"Position: {position_pct:.1%} of portfolio",
            f"Stop loss: -{stop_loss_pct:.1%}, Take profit: +{take_profit_pct:.1%}",
        ]

        # Add LLM reasoning if available
        if signal.llm_reasoning:
            reasoning_parts.append(f"LLM: {signal.llm_reasoning[:100]}")

        return StrategyAction(
            action=action,
            token=signal.token,
            chain=signal.chain,
            position_size_pct=position_pct,
            stop_loss=1 - stop_loss_pct if risk.liquidity_score > 0 else None,
            take_profit=1 + take_profit_pct if risk.liquidity_score > 0 else None,
            reasoning=" | ".join(reasoning_parts),
        )
