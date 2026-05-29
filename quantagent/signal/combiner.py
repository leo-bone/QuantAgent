"""Signal combiner - fuses signals from multiple sources into a CombinedSignal.

Implements adaptive weighted signal fusion with conflict resolution:
- Technical signals: base weight 0.3
- On-chain signals: base weight 0.4 (higher because harder to fake)
- LLM signals: base weight 0.3 (contextual but can be wrong)
- When signals conflict, lower-confidence signals are down-weighted
- Weights adapt over time based on each source's prediction accuracy
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from quantagent.common.models import (
    Chain,
    CombinedSignal,
    Signal,
    SignalSource,
    SignalType,
)
from quantagent.signal.adaptive_weights import AdaptiveWeightEngine

# Default weights for each signal source
DEFAULT_WEIGHTS = {
    SignalSource.TECHNICAL: 0.3,
    SignalSource.ONCHAIN: 0.4,
    SignalSource.LLM: 0.3,
}


class SignalCombiner:
    """Fuses signals from multiple sources into a unified CombinedSignal.

    Supports both static weights (legacy) and adaptive weights that learn
    from historical prediction accuracy.
    """

    def __init__(
        self,
        weights: dict[SignalSource, float] | None = None,
        conflict_threshold: float = 0.3,
        adaptive: bool = True,
    ):
        """
        Args:
            weights: Custom base weights per signal source. Must sum to 1.0.
            conflict_threshold: Minimum confidence difference to consider
                signals as conflicting.
            adaptive: Enable adaptive weight adjustment based on accuracy.
        """
        self.weights = weights or DEFAULT_WEIGHTS
        self.conflict_threshold = conflict_threshold
        self.adaptive = adaptive

        # Initialize adaptive engine if enabled
        self._adaptive_engine: AdaptiveWeightEngine | None = None
        if adaptive:
            self._adaptive_engine = AdaptiveWeightEngine(base_weights=self.weights)

    @property
    def effective_weights(self) -> dict[SignalSource, float]:
        """Get current effective weights (adaptive or static)."""
        if self._adaptive_engine:
            return self._adaptive_engine.get_weights()
        return self.weights

    def record_outcome(
        self,
        source: SignalSource,
        predicted: SignalType,
        actual: SignalType,
    ) -> None:
        """Record signal outcome for adaptive weight learning.

        Args:
            source: Which signal source made the prediction
            predicted: What the signal predicted
            actual: What actually happened
        """
        if self._adaptive_engine:
            self._adaptive_engine.record_outcome(source, predicted, actual)

    def get_adaptive_stats(self) -> dict:
        """Get adaptive weight engine statistics."""
        if self._adaptive_engine:
            return self._adaptive_engine.get_stats()
        return {}

    def get_weight_summary(self) -> str:
        """Human-readable weight summary."""
        if self._adaptive_engine:
            return self._adaptive_engine.get_weight_summary()
        lines = ["📊 Static Signal Weights:"]
        for source, w in self.weights.items():
            lines.append(f"  {source.value:12s}: weight={w:.3f}")
        return "\n".join(lines)

    def combine(
        self,
        token: str,
        chain: Chain,
        technical_signals: list[Signal],
        onchain_signals: list[Signal],
        llm_signal: Signal | None = None,
    ) -> CombinedSignal:
        """Combine signals from all sources into a unified signal.

        Algorithm:
        1. Get effective weights (adaptive or static)
        2. Calculate weighted score for each direction (bullish, bearish, neutral)
        3. Handle conflicts by down-weighting low-confidence opposing signals
        4. Select the direction with highest score
        5. Calculate final confidence as the weighted score
        """
        # Use adaptive weights if available
        current_weights = self.effective_weights

        # Group signals by source
        all_signals = list(technical_signals) + list(onchain_signals)
        if llm_signal:
            all_signals.append(llm_signal)

        if not all_signals:
            return CombinedSignal(
                token=token,
                chain=chain,
                signal_type=SignalType.NEUTRAL,
                confidence=0.0,
            )

        # Calculate weighted scores for each direction
        scores = {SignalType.BULLISH: 0.0, SignalType.BEARISH: 0.0, SignalType.NEUTRAL: 0.0}
        signal_counts = {SignalType.BULLISH: 0, SignalType.BEARISH: 0, SignalType.NEUTRAL: 0}

        for signal in all_signals:
            weight = current_weights.get(signal.source, 0.2)

            # Adjust weight based on confidence
            adjusted_weight = weight * signal.confidence

            # Check for conflicts with other signals
            opposing_signals = [
                s for s in all_signals
                if s.signal_type != signal.signal_type
                and s.confidence > signal.confidence + self.conflict_threshold
            ]
            if opposing_signals:
                # Down-weight this signal since stronger opposing signals exist
                adjustment = 1.0 - (0.5 * len(opposing_signals) / max(len(all_signals), 1))
                adjusted_weight *= max(adjustment, 0.2)

            scores[signal.signal_type] += adjusted_weight
            signal_counts[signal.signal_type] += 1

        # Select the direction with highest score
        best_direction = max(scores, key=scores.get)
        total_score = sum(scores.values())
        final_confidence = scores[best_direction] / total_score if total_score > 0 else 0.0

        # Generate LLM reasoning summary if available
        llm_reasoning = ""
        if llm_signal and llm_signal.value and isinstance(llm_signal.value, dict):
            llm_reasoning = llm_signal.value.get("reasoning", "")

        combined = CombinedSignal(
            token=token,
            chain=chain,
            signal_type=best_direction,
            confidence=round(final_confidence, 3),
            technical_signals=technical_signals,
            onchain_signals=onchain_signals,
            llm_signal=llm_signal,
            llm_reasoning=llm_reasoning,
        )

        # Build weight info for logging
        weight_str = ", ".join(
            f"{s.value[:3]}={w:.2f}" for s, w in current_weights.items()
        )
        logger.info(
            f"[{token}] Combined signal: {best_direction.value} "
            f"(confidence: {final_confidence:.2%}) "
            f"| Tech: {len(technical_signals)}, On-chain: {len(onchain_signals)}, "
            f"LLM: {'yes' if llm_signal else 'no'} "
            f"| Weights: [{weight_str}]"
        )

        return combined
