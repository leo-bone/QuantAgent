"""
Strategy Genome — Encodes trading strategy parameters as evolvable genes.

Each strategy is represented as a genome (collection of genes), where each gene
controls a specific aspect of the trading system. The genome can be mutated,
crossed over, and evaluated for fitness.

Architecture:
    Gene → StrategyGenome → GenomeDecoder → Runtime Parameters

Gene Categories:
    - Signal Weights: technical, onchain, llm weights
    - Technical Indicators: RSI periods, MACD params, BB multipliers
    - Risk Parameters: max position, stop loss, take profit ratios
    - Entry Rules: confidence thresholds, signal combination rules
    - Exit Rules: trailing stop, time-based exit
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from quantagent.common.models import Chain


# ---------------------------------------------------------------------------
# Gene — the atomic unit of evolution
# ---------------------------------------------------------------------------

class GeneType(str, Enum):
    """Category of gene."""
    SIGNAL_WEIGHT = "signal_weight"
    TECHNICAL_PARAM = "technical_param"
    RISK_PARAM = "risk_param"
    ENTRY_RULE = "entry_rule"
    EXIT_RULE = "exit_rule"


@dataclass
class Gene:
    """A single evolvable parameter.

    Attributes:
        name: Human-readable gene name (e.g., "technical_weight")
        gene_type: Category of this gene
        value: Current value
        min_value: Lower bound
        max_value: Upper bound
        mutation_rate: Probability of mutation per generation (0.0-1.0)
        mutation_sigma: Std dev for Gaussian mutation (relative to range)
    """
    name: str
    gene_type: GeneType
    value: float
    min_value: float
    max_value: float
    mutation_rate: float = 0.3
    mutation_sigma: float = 0.15  # 15% of value range

    @property
    def range_size(self) -> float:
        return self.max_value - self.min_value

    def clamp(self) -> "Gene":
        """Clamp value to [min_value, max_value]."""
        self.value = max(self.min_value, min(self.max_value, self.value))
        return self

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "gene_type": self.gene_type.value,
            "value": self.value,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "mutation_rate": self.mutation_rate,
            "mutation_sigma": self.mutation_sigma,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Gene":
        return cls(
            name=d["name"],
            gene_type=GeneType(d["gene_type"]),
            value=d["value"],
            min_value=d["min_value"],
            max_value=d["max_value"],
            mutation_rate=d.get("mutation_rate", 0.3),
            mutation_sigma=d.get("mutation_sigma", 0.15),
        )


# ---------------------------------------------------------------------------
# StrategyGenome — complete evolvable strategy
# ---------------------------------------------------------------------------

@dataclass
class StrategyGenome:
    """A complete strategy encoded as a genome of genes.

    The genome defines all tuneable parameters of the trading system.
    Each genome has a unique ID and tracks its generation and fitness.
    """
    genes: list[Gene] = field(default_factory=list)
    genome_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)
    fitness_score: Optional[float] = None
    fitness_result: Optional[Any] = None  # FitnessResult, avoided circular import

    # --- Gene Access ---

    def get_gene(self, name: str) -> Optional[Gene]:
        """Get a gene by name."""
        for g in self.genes:
            if g.name == name:
                return g
        return None

    def get_value(self, name: str, default: float = 0.0) -> float:
        """Get gene value by name."""
        g = self.get_gene(name)
        return g.value if g else default

    def set_value(self, name: str, value: float) -> None:
        """Set gene value by name (clamps to bounds)."""
        g = self.get_gene(name)
        if g:
            g.value = value
            g.clamp()

    def get_genes_by_type(self, gene_type: GeneType) -> list[Gene]:
        """Get all genes of a specific type."""
        return [g for g in self.genes if g.gene_type == gene_type]

    # --- Serialization ---

    def to_dict(self) -> dict:
        return {
            "genome_id": self.genome_id,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "fitness_score": self.fitness_score,
            "genes": [g.to_dict() for g in self.genes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyGenome":
        genome = cls(
            genes=[Gene.from_dict(g) for g in d["genes"]],
            genome_id=d["genome_id"],
            generation=d["generation"],
            parent_ids=d.get("parent_ids", []),
            fitness_score=d.get("fitness_score"),
        )
        return genome

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "StrategyGenome":
        return cls.from_dict(json.loads(s))

    def clone(self) -> "StrategyGenome":
        """Deep copy of this genome with a new ID."""
        new = copy.deepcopy(self)
        new.genome_id = str(uuid.uuid4())[:8]
        new.parent_ids = [self.genome_id]
        new.fitness_score = None
        new.fitness_result = None
        return new

    def summary(self) -> dict:
        """Human-readable summary of the genome."""
        return {
            "id": self.genome_id,
            "generation": self.generation,
            "parents": self.parent_ids,
            "fitness": round(self.fitness_score, 4) if self.fitness_score else None,
            "gene_count": len(self.genes),
            "params": {g.name: round(g.value, 4) for g in self.genes},
        }


# ---------------------------------------------------------------------------
# GenomeDecoder — converts genome to runtime parameters
# ---------------------------------------------------------------------------

class GenomeDecoder:
    """Decodes a StrategyGenome into parameters consumable by the trading system.

    This is the bridge between the evolution layer and the execution layer.
    The decoder knows the schema of the genome and maps genes to the specific
    parameters each component expects.
    """

    @staticmethod
    def decode_signal_weights(genome: StrategyGenome) -> dict[str, float]:
        """Decode signal source weights from genome, normalized to sum=1.0."""
        raw = {
            "technical": genome.get_value("weight_technical", 0.3),
            "onchain": genome.get_value("weight_onchain", 0.4),
            "llm": genome.get_value("weight_llm", 0.3),
        }
        total = sum(raw.values())
        if total <= 0:
            return {"technical": 0.33, "onchain": 0.34, "llm": 0.33}
        return {k: v / total for k, v in raw.items()}

    @staticmethod
    def decode_technical_params(genome: StrategyGenome) -> dict:
        """Decode technical indicator parameters."""
        return {
            "rsi_period": int(genome.get_value("rsi_period", 14)),
            "rsi_overbought": genome.get_value("rsi_overbought", 70),
            "rsi_oversold": genome.get_value("rsi_oversold", 30),
            "macd_fast": int(genome.get_value("macd_fast", 12)),
            "macd_slow": int(genome.get_value("macd_slow", 26)),
            "macd_signal": int(genome.get_value("macd_signal_period", 9)),
            "bb_period": int(genome.get_value("bb_period", 20)),
            "bb_std": genome.get_value("bb_std", 2.0),
            "atr_period": int(genome.get_value("atr_period", 14)),
        }

    @staticmethod
    def decode_risk_params(genome: StrategyGenome) -> dict:
        """Decode risk management parameters."""
        return {
            "max_position_pct": genome.get_value("max_position_pct", 0.10),
            "stop_loss_pct": genome.get_value("stop_loss_pct", 0.05),
            "take_profit_ratio": genome.get_value("take_profit_ratio", 2.0),
            "trailing_stop_pct": genome.get_value("trailing_stop_pct", 0.03),
            "max_drawdown_pct": genome.get_value("max_drawdown_pct", 0.15),
            "daily_loss_limit_pct": genome.get_value("daily_loss_limit_pct", 0.05),
        }

    @staticmethod
    def decode_entry_rules(genome: StrategyGenome) -> dict:
        """Decode entry signal rules."""
        return {
            "min_confidence": genome.get_value("min_confidence", 0.6),
            "bullish_threshold": genome.get_value("bullish_threshold", 0.55),
            "risk_score_max": genome.get_value("risk_score_max", 0.7),
            "volume_surge_factor": genome.get_value("volume_surge_factor", 1.5),
        }

    @staticmethod
    def decode_exit_rules(genome: StrategyGenome) -> dict:
        """Decode exit rules."""
        return {
            "trailing_stop_activation_pct": genome.get_value(
                "trailing_stop_activation_pct", 0.03
            ),
            "time_exit_hours": int(genome.get_value("time_exit_hours", 48)),
            "momentum_decay_threshold": genome.get_value(
                "momentum_decay_threshold", 0.3
            ),
        }

    @staticmethod
    def decode_all(genome: StrategyGenome) -> dict:
        """Decode all parameters from a genome."""
        return {
            "signal_weights": GenomeDecoder.decode_signal_weights(genome),
            "technical_params": GenomeDecoder.decode_technical_params(genome),
            "risk_params": GenomeDecoder.decode_risk_params(genome),
            "entry_rules": GenomeDecoder.decode_entry_rules(genome),
            "exit_rules": GenomeDecoder.decode_exit_rules(genome),
        }


# ---------------------------------------------------------------------------
# Genome Factory — creates default and random genomes
# ---------------------------------------------------------------------------

class GenomeFactory:
    """Factory for creating StrategyGenome instances."""

    @staticmethod
    def default_genome() -> StrategyGenome:
        """Create the default genome with baseline parameters."""
        genes = [
            # Signal weights
            Gene("weight_technical", GeneType.SIGNAL_WEIGHT, 0.30, 0.10, 0.60, 0.4, 0.20),
            Gene("weight_onchain",   GeneType.SIGNAL_WEIGHT, 0.40, 0.10, 0.60, 0.4, 0.20),
            Gene("weight_llm",       GeneType.SIGNAL_WEIGHT, 0.30, 0.10, 0.60, 0.4, 0.20),

            # Technical indicator params
            Gene("rsi_period",       GeneType.TECHNICAL_PARAM, 14, 5, 30, 0.3, 0.15),
            Gene("rsi_overbought",   GeneType.TECHNICAL_PARAM, 70, 60, 90, 0.3, 0.10),
            Gene("rsi_oversold",     GeneType.TECHNICAL_PARAM, 30, 10, 40, 0.3, 0.10),
            Gene("macd_fast",        GeneType.TECHNICAL_PARAM, 12, 5, 20, 0.3, 0.15),
            Gene("macd_slow",        GeneType.TECHNICAL_PARAM, 26, 15, 50, 0.3, 0.15),
            Gene("macd_signal_period", GeneType.TECHNICAL_PARAM, 9, 3, 15, 0.3, 0.15),
            Gene("bb_period",        GeneType.TECHNICAL_PARAM, 20, 10, 40, 0.3, 0.15),
            Gene("bb_std",           GeneType.TECHNICAL_PARAM, 2.0, 1.0, 3.5, 0.3, 0.15),
            Gene("atr_period",       GeneType.TECHNICAL_PARAM, 14, 5, 30, 0.2, 0.15),

            # Risk parameters
            Gene("max_position_pct",      GeneType.RISK_PARAM, 0.10, 0.02, 0.25, 0.3, 0.15),
            Gene("stop_loss_pct",         GeneType.RISK_PARAM, 0.05, 0.01, 0.15, 0.3, 0.15),
            Gene("take_profit_ratio",     GeneType.RISK_PARAM, 2.0, 1.0, 5.0, 0.3, 0.15),
            Gene("trailing_stop_pct",     GeneType.RISK_PARAM, 0.03, 0.01, 0.10, 0.3, 0.15),
            Gene("max_drawdown_pct",      GeneType.RISK_PARAM, 0.15, 0.05, 0.30, 0.2, 0.10),
            Gene("daily_loss_limit_pct",  GeneType.RISK_PARAM, 0.05, 0.01, 0.15, 0.2, 0.10),

            # Entry rules
            Gene("min_confidence",        GeneType.ENTRY_RULE, 0.60, 0.40, 0.90, 0.3, 0.15),
            Gene("bullish_threshold",     GeneType.ENTRY_RULE, 0.55, 0.40, 0.80, 0.3, 0.15),
            Gene("risk_score_max",        GeneType.ENTRY_RULE, 0.70, 0.30, 0.90, 0.2, 0.10),
            Gene("volume_surge_factor",   GeneType.ENTRY_RULE, 1.50, 1.0, 3.0, 0.3, 0.15),

            # Exit rules
            Gene("trailing_stop_activation_pct", GeneType.EXIT_RULE, 0.03, 0.01, 0.10, 0.3, 0.15),
            Gene("time_exit_hours",              GeneType.EXIT_RULE, 48, 6, 168, 0.2, 0.15),
            Gene("momentum_decay_threshold",     GeneType.EXIT_RULE, 0.30, 0.10, 0.60, 0.3, 0.15),
        ]
        return StrategyGenome(genes=genes, generation=0)

    @staticmethod
    def random_genome(rng: Optional[Any] = None) -> StrategyGenome:
        """Create a genome with random values within bounds."""
        import random
        _rng = rng or random

        base = GenomeFactory.default_genome()
        for gene in base.genes:
            gene.value = _rng.uniform(gene.min_value, gene.max_value)
        base.genome_id = str(uuid.uuid4())[:8]
        return base

    @staticmethod
    def create_population(size: int = 20) -> list[StrategyGenome]:
        """Create an initial population with 1 default + (size-1) random genomes."""
        population = [GenomeFactory.default_genome()]
        for _ in range(size - 1):
            population.append(GenomeFactory.random_genome())
        return population
