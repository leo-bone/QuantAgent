"""Adaptive signal weight engine — dynamically adjusts signal source weights
based on historical prediction accuracy.

Core idea: Signal sources that have been more accurate recently should receive
higher weight. This creates a self-improving system that learns which signals
to trust more over time.

Algorithm:
- Track per-source hit rate over a rolling window (default: last 100 signals)
- Adjust weights using exponential moving average of accuracy
- Add regularization to prevent any single source from dominating (>0.6)
- Decay old performance so recent accuracy matters more
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from quantagent.common.models import (
    SignalSource,
    SignalType,
)

# Default base weights (same as before for backward compat)
DEFAULT_BASE_WEIGHTS = {
    SignalSource.TECHNICAL: 0.3,
    SignalSource.ONCHAIN: 0.4,
    SignalSource.LLM: 0.3,
}

# Adaptive constraints
MAX_SINGLE_WEIGHT = 0.60  # No source can exceed 60%
MIN_SINGLE_WEIGHT = 0.10  # No source can drop below 10%
ADAPTATION_RATE = 0.15     # How fast weights adjust (0 = static, 1 = instant)
ROLLING_WINDOW = 100       # Number of recent signals to consider
MIN_SAMPLES = 10           # Minimum samples before adaptation kicks in


class AdaptiveWeightEngine:
    """Dynamically adjusts signal source weights based on historical accuracy.

    Usage:
        engine = AdaptiveWeightEngine()
        # ... generate signals, get combined result ...
        engine.record_outcome(source=SignalSource.TECHNICAL, predicted=SignalType.BULLISH, actual=SignalType.BULLISH)
        weights = engine.get_weights()  # Returns adjusted weights
    """

    def __init__(
        self,
        base_weights: dict[SignalSource, float] | None = None,
        adaptation_rate: float = ADAPTATION_RATE,
        max_weight: float = MAX_SINGLE_WEIGHT,
        min_weight: float = MIN_SINGLE_WEIGHT,
        rolling_window: int = ROLLING_WINDOW,
        min_samples: int = MIN_SAMPLES,
    ):
        self.base_weights = base_weights or DEFAULT_BASE_WEIGHTS
        self.adaptation_rate = adaptation_rate
        self.max_weight = max_weight
        self.min_weight = min_weight
        self.rolling_window = rolling_window
        self.min_samples = min_samples

        # Rolling accuracy tracker per source
        # Each entry: (predicted: SignalType, actual: SignalType, timestamp: float)
        self._history: dict[SignalSource, deque] = {
            source: deque(maxlen=rolling_window)
            for source in self.base_weights
        }

        # Current adaptive weights (starts at base)
        self._current_weights = dict(self.base_weights)

        # Performance stats for observability
        self._stats: dict[SignalSource, dict] = {
            source: {
                "total": 0,
                "correct": 0,
                "hit_rate": 0.5,  # Prior of 50% (no information)
                "last_updated": None,
            }
            for source in self.base_weights
        }

    def record_outcome(
        self,
        source: SignalSource,
        predicted: SignalType,
        actual: SignalType,
    ) -> None:
        """Record the outcome of a signal prediction.

        Args:
            source: Which signal source made the prediction
            predicted: What the signal predicted (BULLISH/BEARISH/NEUTRAL)
            actual: What actually happened
        """
        if source not in self._history:
            logger.warning(f"Unknown signal source: {source}, skipping")
            return

        now = datetime.now(timezone.utc).timestamp()
        self._history[source].append((predicted, actual, now))

        # Recalculate hit rate
        history = self._history[source]
        total = len(history)
        correct = sum(1 for p, a, _ in history if p == a)
        hit_rate = correct / total if total > 0 else 0.5

        # Update stats
        self._stats[source]["total"] = total
        self._stats[source]["correct"] = correct
        self._stats[source]["hit_rate"] = hit_rate
        self._stats[source]["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Recompute adaptive weights
        self._update_weights()

        logger.debug(
            f"[AdaptiveWeight] {source.value}: hit_rate={hit_rate:.2%} "
            f"({correct}/{total}), weight={self._current_weights[source]:.3f}"
        )

    def get_weights(self) -> dict[SignalSource, float]:
        """Get current adaptive weights (sums to 1.0)."""
        return dict(self._current_weights)

    def get_stats(self) -> dict[SignalSource, dict]:
        """Get per-source accuracy statistics."""
        return {
            source: dict(stats)
            for source, stats in self._stats.items()
        }

    def _update_weights(self) -> None:
        """Recompute weights based on recent accuracy.

        Algorithm:
        1. Calculate hit rate for each source over rolling window
        2. Compute raw adaptive weight = base_weight * (hit_rate / avg_hit_rate)
        3. Apply soft constraints (clamp to [min_weight, max_weight])
        4. Normalize so weights sum to 1.0
        """
        # Only adapt if we have enough samples across sources
        sources_with_data = sum(
            1 for h in self._history.values() if len(h) >= self.min_samples
        )
        if sources_with_data < 1:
            # Not enough data yet, keep base weights
            return

        # Step 1: Get hit rates
        hit_rates = {}
        for source, history in self._history.items():
            if len(history) >= self.min_samples:
                total = len(history)
                correct = sum(1 for p, a, _ in history if p == a)
                hit_rates[source] = correct / total
            else:
                # Not enough data, use prior
                hit_rates[source] = 0.5

        # Step 2: Compute weighted adjustment
        avg_hit_rate = sum(hit_rates.values()) / len(hit_rates) if hit_rates else 0.5
        if avg_hit_rate == 0:
            avg_hit_rate = 0.01  # Prevent division by zero

        raw_weights = {}
        for source, base_w in self.base_weights.items():
            # Accuracy ratio: how much better/worse than average
            accuracy_ratio = hit_rates.get(source, 0.5) / avg_hit_rate

            # Smooth adaptation: blend base weight with accuracy-adjusted weight
            adapted_w = base_w * accuracy_ratio
            blended_w = (1 - self.adaptation_rate) * base_w + self.adaptation_rate * adapted_w

            raw_weights[source] = blended_w

        # Step 3: Apply constraints
        for source in raw_weights:
            raw_weights[source] = max(self.min_weight, min(self.max_weight, raw_weights[source]))

        # Step 4: Normalize to sum to 1.0
        total_weight = sum(raw_weights.values())
        if total_weight > 0:
            self._current_weights = {
                source: w / total_weight
                for source, w in raw_weights.items()
            }

    def reset(self) -> None:
        """Reset to base weights and clear history."""
        self._current_weights = dict(self.base_weights)
        for source in self._history:
            self._history[source].clear()
        for source in self._stats:
            self._stats[source] = {
                "total": 0,
                "correct": 0,
                "hit_rate": 0.5,
                "last_updated": None,
            }

    def get_weight_summary(self) -> str:
        """Human-readable summary of current weights and accuracy."""
        lines = ["📊 Adaptive Signal Weights:"]
        for source in self.base_weights:
            w = self._current_weights.get(source, 0)
            base = self.base_weights[source]
            stats = self._stats[source]
            hr = stats["hit_rate"]
            n = stats["total"]
            delta = w - base
            arrow = "↑" if delta > 0.01 else "↓" if delta < -0.01 else "→"
            lines.append(
                f"  {source.value:12s}: weight={w:.3f} (base {base:.1f} {arrow}{abs(delta):.3f}) "
                f"| hit_rate={hr:.1%} ({n} samples)"
            )
        return "\n".join(lines)
