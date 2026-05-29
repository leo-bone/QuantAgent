"""
Self-Reflection Module — LLM-guided strategy improvement.

After each trading cycle (or batch of trades), the agent reflects on:
    1. What worked and what didn't
    2. Which signals were accurate vs misleading
    3. Market regime changes that affected performance
    4. Risk management effectiveness

The reflection generates directed mutation suggestions that feed into
the evolution engine, closing the learning loop:

    Trade → Reflect → Suggest Mutations → Evolve → Better Strategy

This is the "self-aware" component that makes QuantAgent more than
a simple genetic algorithm — it can *reason* about its failures.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from quantagent.evolution.genome import StrategyGenome, GenomeDecoder

logger = logging.getLogger(__name__)


REFLECTION_SYSTEM_PROMPT = """You are a quantitative trading strategy analyst. Your job is to analyze 
recent trading performance and suggest specific parameter adjustments.

You will receive:
1. Recent trade outcomes (wins/losses with details)
2. Current strategy parameters
3. Signal accuracy breakdown

You must respond in JSON format with:
{
    "analysis": "Brief analysis of what happened",
    "market_regime": "trending_up|trending_down|ranging|volatile",
    "parameter_suggestions": [
        {
            "parameter": "gene_name",
            "direction": 1 or -1,   // 1=increase, -1=decrease
            "strength": 0.1-0.5,    // How strongly to push
            "reasoning": "Why this change helps"
        }
    ],
    "risk_assessment": "low|medium|high",
    "strategy_notes": "Additional context"
}

Be specific and data-driven. Focus on the 2-3 most impactful changes.
Do NOT suggest changes to parameters that performed well.
"""


@dataclass
class ReflectionSuggestion:
    """A single parameter adjustment suggestion from reflection."""
    parameter: str
    direction: float  # Positive = increase, Negative = decrease
    strength: float   # 0.1 - 0.5
    reasoning: str


@dataclass
class ReflectionResult:
    """Complete reflection result."""
    analysis: str
    market_regime: str
    suggestions: list[ReflectionSuggestion] = field(default_factory=list)
    risk_assessment: str = "medium"
    strategy_notes: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "analysis": self.analysis,
            "market_regime": self.market_regime,
            "suggestions": [
                {
                    "parameter": s.parameter,
                    "direction": s.direction,
                    "strength": s.strength,
                    "reasoning": s.reasoning,
                }
                for s in self.suggestions
            ],
            "risk_assessment": self.risk_assessment,
            "strategy_notes": self.strategy_notes,
            "timestamp": self.timestamp.isoformat(),
        }


class SelfReflection:
    """Analyzes trading performance and generates improvement suggestions.

    The reflection engine:
        1. Collects recent trade data and signal accuracy
        2. Sends to LLM for analysis
        3. Parses structured suggestions
        4. Converts to directed mutations for the evolution engine

    Integration with evolution:
        - After N trades, trigger reflection
        - Feed suggestions as directed_mutations to the next generation
        - Creates a hybrid of random exploration + informed exploitation
    """

    def __init__(
        self,
        reflection_interval: int = 20,  # Reflect every N trades
        min_trades_for_reflection: int = 5,
    ):
        self.reflection_interval = reflection_interval
        self.min_trades_for_reflection = min_trades_for_reflection
        self.trade_buffer: list[dict] = []
        self.last_reflection: Optional[ReflectionResult] = None
        self.reflection_history: list[ReflectionResult] = []

    def record_trade(
        self,
        token: str,
        action: str,       # "buy" or "sell"
        pnl_pct: float,    # Percentage P&L
        entry_reason: str,  # What triggered the entry
        exit_reason: str,   # What triggered the exit
        hold_duration_hours: float = 0.0,
        signal_confidence: float = 0.0,
    ) -> bool:
        """Record a completed trade. Returns True if reflection should trigger."""
        self.trade_buffer.append({
            "token": token,
            "action": action,
            "pnl_pct": round(pnl_pct, 4),
            "entry_reason": entry_reason,
            "exit_reason": exit_reason,
            "hold_duration_hours": round(hold_duration_hours, 1),
            "signal_confidence": round(signal_confidence, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        should_reflect = (
            len(self.trade_buffer) >= self.min_trades_for_reflection
            and len(self.trade_buffer) % self.reflection_interval == 0
        )
        return should_reflect

    async def reflect(
        self,
        current_genome: Optional[StrategyGenome] = None,
        signal_accuracy: Optional[dict] = None,
    ) -> ReflectionResult:
        """Perform reflection on recent trades and generate suggestions.

        Args:
            current_genome: The currently active strategy genome
            signal_accuracy: Dict of signal source -> accuracy stats

        Returns:
            ReflectionResult with analysis and suggestions
        """
        if len(self.trade_buffer) < self.min_trades_for_reflection:
            return ReflectionResult(
                analysis="Insufficient trade data for meaningful reflection",
                market_regime="unknown",
            )

        # Build context
        trades_summary = self._summarize_trades()
        genome_summary = (
            GenomeDecoder.decode_all(current_genome) if current_genome else {}
        )

        # Try LLM-based reflection
        try:
            result = await self._llm_reflect(trades_summary, genome_summary, signal_accuracy)
        except Exception as e:
            logger.warning(f"LLM reflection failed, using rule-based fallback: {e}")
            result = self._rule_based_reflect(trades_summary, genome_summary)

        self.last_reflection = result
        self.reflection_history.append(result)

        # Clear buffer after reflection
        self.trade_buffer = self.trade_buffer[-5:]  # Keep last 5 for context

        return result

    async def _llm_reflect(
        self,
        trades_summary: dict,
        genome_summary: dict,
        signal_accuracy: Optional[dict],
    ) -> ReflectionResult:
        """Use LLM to analyze trades and suggest parameter changes."""
        try:
            from quantagent.signal.llm_analyzer import LLMSignalAnalyzer
            from quantagent.infrastructure.circuit_breaker import CircuitBreaker
        except ImportError:
            return self._rule_based_reflect(trades_summary, genome_summary)

        # Build the reflection prompt
        user_prompt = f"""Analyze the following trading performance data and suggest parameter adjustments.

## Recent Trades Summary
{json.dumps(trades_summary, indent=2)}

## Current Strategy Parameters
{json.dumps(genome_summary, indent=2)}

## Signal Accuracy
{json.dumps(signal_accuracy or {}, indent=2)}

Respond in the exact JSON format specified in your system prompt."""

        # Use the LLM analyzer's client if available
        try:
            import os
            from openai import AsyncOpenAI

            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
            if not api_key:
                return self._rule_based_reflect(trades_summary, genome_summary)

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv("LLM_BASE_URL"),
            )

            import asyncio
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                    messages=[
                        {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=1000,
                    response_format={"type": "json_object"},
                ),
                timeout=30.0,
            )

            content = response.choices[0].message.content
            parsed = json.loads(content)

            suggestions = []
            for s in parsed.get("parameter_suggestions", []):
                suggestions.append(ReflectionSuggestion(
                    parameter=s["parameter"],
                    direction=float(s["direction"]),
                    strength=float(s.get("strength", 0.2)),
                    reasoning=s.get("reasoning", ""),
                ))

            return ReflectionResult(
                analysis=parsed.get("analysis", ""),
                market_regime=parsed.get("market_regime", "unknown"),
                suggestions=suggestions,
                risk_assessment=parsed.get("risk_assessment", "medium"),
                strategy_notes=parsed.get("strategy_notes", ""),
            )

        except Exception as e:
            logger.warning(f"LLM reflection call failed: {e}")
            return self._rule_based_reflect(trades_summary, genome_summary)

    def _rule_based_reflect(
        self,
        trades_summary: dict,
        genome_summary: dict,
    ) -> ReflectionResult:
        """Fallback rule-based reflection when LLM is unavailable.

        Uses simple heuristics:
            - Low win rate → increase min_confidence, tighten stop_loss
            - High max drawdown → reduce max_position_pct, tighten stops
            - Many quick losses → increase min_confidence threshold
            - Long holds that lose → reduce time_exit_hours, add trailing stop
        """
        suggestions: list[ReflectionSuggestion] = []
        win_rate = trades_summary.get("win_rate", 0.5)
        avg_pnl = trades_summary.get("avg_pnl_pct", 0.0)
        max_loss = trades_summary.get("max_loss_pct", 0.0)
        avg_duration = trades_summary.get("avg_duration_hours", 24.0)

        analysis_parts = []
        regime = "ranging"

        # Low win rate
        if win_rate < 0.4:
            suggestions.append(ReflectionSuggestion(
                parameter="min_confidence",
                direction=1,
                strength=0.3,
                reasoning=f"Win rate is {win_rate:.0%}, too low. Increase entry confidence threshold.",
            ))
            suggestions.append(ReflectionSuggestion(
                parameter="stop_loss_pct",
                direction=-1,
                strength=0.2,
                reasoning="Tighten stop losses to cut losing trades faster.",
            ))
            analysis_parts.append(f"Low win rate ({win_rate:.0%})")

        # High drawdown (indirect via large losses)
        if max_loss < -0.10:
            suggestions.append(ReflectionSuggestion(
                parameter="max_position_pct",
                direction=-1,
                strength=0.3,
                reasoning=f"Max single loss is {max_loss:.1%}, reduce position size.",
            ))
            suggestions.append(ReflectionSuggestion(
                parameter="trailing_stop_pct",
                direction=-1,
                strength=0.2,
                reasoning="Tighten trailing stop to protect gains.",
            ))
            analysis_parts.append(f"Large losses detected (max {max_loss:.1%})")

        # Quick losses — reduce entry frequency
        if avg_duration < 6 and avg_pnl < 0:
            suggestions.append(ReflectionSuggestion(
                parameter="bullish_threshold",
                direction=1,
                strength=0.2,
                reasoning="Trades exit quickly with losses. Require stronger signals.",
            ))
            regime = "volatile"
            analysis_parts.append("Quick losses — volatile market")

        # Long holds losing
        if avg_duration > 48 and avg_pnl < 0:
            suggestions.append(ReflectionSuggestion(
                parameter="time_exit_hours",
                direction=-1,
                strength=0.2,
                reasoning="Long holds are losing. Reduce max hold time.",
            ))
            suggestions.append(ReflectionSuggestion(
                parameter="momentum_decay_threshold",
                direction=-1,
                strength=0.15,
                reasoning="Exit earlier when momentum fades.",
            ))
            regime = "ranging"
            analysis_parts.append("Long holds losing — ranging market")

        # Good performance — can afford to be slightly more aggressive
        if win_rate > 0.6 and avg_pnl > 0:
            suggestions.append(ReflectionSuggestion(
                parameter="max_position_pct",
                direction=1,
                strength=0.1,
                reasoning="Good performance, can slightly increase position size.",
            ))
            regime = "trending_up" if avg_pnl > 0.02 else "trending_down"
            analysis_parts.append(f"Good performance (WR {win_rate:.0%}, avg PnL {avg_pnl:.2%})")

        analysis = "; ".join(analysis_parts) if analysis_parts else "Performance within normal range"

        return ReflectionResult(
            analysis=analysis,
            market_regime=regime,
            suggestions=suggestions,
            risk_assessment="high" if win_rate < 0.3 else "medium" if win_rate < 0.5 else "low",
            strategy_notes=f"Rule-based reflection based on {trades_summary.get('total_trades', 0)} trades",
        )

    def _summarize_trades(self) -> dict:
        """Summarize recent trades for reflection."""
        if not self.trade_buffer:
            return {"total_trades": 0}

        pnls = [t["pnl_pct"] for t in self.trade_buffer]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        durations = [t["hold_duration_hours"] for t in self.trade_buffer]

        return {
            "total_trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0,
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else 0,
            "max_win_pct": round(max(pnls), 4) if pnls else 0,
            "max_loss_pct": round(min(pnls), 4) if pnls else 0,
            "avg_duration_hours": round(sum(durations) / len(durations), 1) if durations else 0,
            "exit_reasons": {
                "stop_loss": sum(1 for t in self.trade_buffer if t["exit_reason"] == "stop_loss"),
                "take_profit": sum(1 for t in self.trade_buffer if t["exit_reason"] == "take_profit"),
                "time_exit": sum(1 for t in self.trade_buffer if t["exit_reason"] == "time_exit"),
                "signal_reversal": sum(1 for t in self.trade_buffer if t["exit_reason"] == "signal_reversal"),
            },
            "avg_confidence": round(
                sum(t["signal_confidence"] for t in self.trade_buffer) / len(self.trade_buffer), 3
            ),
        }

    def get_suggestions_for_evolution(self) -> list[dict]:
        """Get reflection suggestions formatted for the evolution engine.

        Returns a list of directed mutation dicts that can be applied
        to the next generation's genomes.
        """
        if not self.last_reflection:
            return []

        return [
            {
                "gene_name": s.parameter,
                "direction": s.direction,
                "strength": s.strength,
                "reasoning": s.reasoning,
            }
            for s in self.last_reflection.suggestions
        ]

    def get_status(self) -> dict:
        """Get reflection module status."""
        return {
            "trades_buffered": len(self.trade_buffer),
            "reflection_interval": self.reflection_interval,
            "last_reflection": self.last_reflection.to_dict() if self.last_reflection else None,
            "total_reflections": len(self.reflection_history),
            "pending_suggestions": len(self.get_suggestions_for_evolution()),
        }
