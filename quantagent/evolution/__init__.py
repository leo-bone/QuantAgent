"""
QuantAgent Evolution Engine — Self-Evolving Strategy Optimization.

Enables the agent to continuously improve its trading strategies through:
1. Strategy Genome encoding (parameters as evolvable genes)
2. Fitness evaluation via backtesting
3. Genetic operations (mutation, crossover, selection)
4. Self-reflection and LLM-guided adaptation
5. Automatic deployment of superior strategies

This is the core differentiator for Grant applications — a system that
doesn't just execute strategies, but *evolves* them.
"""

from quantagent.evolution.genome import (
    StrategyGenome,
    Gene,
    GeneType,
    GenomeDecoder,
)
from quantagent.evolution.fitness import FitnessEvaluator, FitnessResult
from quantagent.evolution.operators import (
    MutationOperator,
    CrossoverOperator,
    SelectionOperator,
)
from quantagent.evolution.engine import EvolutionEngine, EvolutionConfig, EvolutionResult
from quantagent.evolution.reflection import SelfReflection

__all__ = [
    "StrategyGenome",
    "Gene",
    "GeneType",
    "GenomeDecoder",
    "FitnessEvaluator",
    "FitnessResult",
    "MutationOperator",
    "CrossoverOperator",
    "SelectionOperator",
    "EvolutionEngine",
    "EvolutionConfig",
    "EvolutionResult",
    "SelfReflection",
]
