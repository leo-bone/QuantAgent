"""Strategy base class - abstract interface for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from quantagent.common.models import (
    Chain,
    CombinedSignal,
    RiskAssessment,
    StrategyAction,
)


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    A strategy evaluates a combined signal and produces an action.
    It must account for risk management constraints.
    """

    name: str = "base"
    description: str = ""

    @abstractmethod
    def evaluate(
        self,
        signal: CombinedSignal,
        risk: RiskAssessment,
        portfolio_state: dict | None = None,
    ) -> StrategyAction:
        """Evaluate a combined signal and produce a trading action.

        Args:
            signal: The fused signal from all sources
            risk: Risk assessment for this trade
            portfolio_state: Current portfolio state (balances, positions, etc.)

        Returns:
            StrategyAction with the recommended action
        """
        ...

    def should_enter(
        self,
        signal: CombinedSignal,
        risk: RiskAssessment,
        min_confidence: float = 0.6,
    ) -> bool:
        """Determine if we should enter a position.

        Default logic: enter only if confidence is high enough and risk is acceptable.
        """
        return (
            signal.confidence >= min_confidence
            and signal.signal_type.value != "neutral"
            and risk.risk_score < 0.7
        )
