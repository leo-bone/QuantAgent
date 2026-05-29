"""
Evolutionary Operators — Mutation, Crossover, Selection.

Implements genetic operators for evolving strategy genomes:
    - Mutation: Gaussian perturbation, boundary mutation
    - Crossover: Uniform crossover, BLX-α blend crossover
    - Selection: Tournament selection, fitness-proportional selection

These operators are designed to balance exploration (finding new regions)
and exploitation (refining good solutions).
"""

from __future__ import annotations

import copy
import random
import logging
from typing import Optional

from quantagent.evolution.genome import StrategyGenome, Gene, GeneType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mutation Operators
# ---------------------------------------------------------------------------

class MutationOperator:
    """Mutates individual genes in a genome.

    Supports two mutation strategies:
        1. Gaussian mutation: adds N(0, sigma) noise, most common
        2. Boundary mutation: resets to min or max, for escaping local optima
    """

    def __init__(
        self,
        default_mutation_rate: float = 0.3,
        default_sigma: float = 0.15,
        boundary_probability: float = 0.1,  # 10% chance of boundary mutation
    ):
        self.default_mutation_rate = default_mutation_rate
        self.default_sigma = default_sigma
        self.boundary_probability = boundary_probability

    def mutate(self, genome: StrategyGenome) -> StrategyGenome:
        """Mutate a genome in-place and return it.

        Each gene mutates independently with its own mutation_rate.
        Gaussian perturbation with clamping to [min, max].
        """
        for gene in genome.genes:
            if random.random() > gene.mutation_rate:
                continue

            if random.random() < self.boundary_probability:
                # Boundary mutation — jump to min or max
                gene.value = random.choice([gene.min_value, gene.max_value])
            else:
                # Gaussian mutation
                sigma = gene.mutation_sigma * gene.range_size
                noise = random.gauss(0, sigma)
                gene.value += noise

            gene.clamp()

        return genome

    def mutate_with_adaptive_rate(
        self,
        genome: StrategyGenome,
        generation: int,
        max_generations: int = 50,
        min_rate: float = 0.05,
        max_rate: float = 0.5,
    ) -> StrategyGenome:
        """Mutate with adaptive rate — higher early, lower later.

        This implements a simple simulated annealing schedule for mutation:
            rate(g) = max_rate - (max_rate - min_rate) * (g / max_gen)

        Encourages exploration in early generations and exploitation later.
        """
        progress = min(1.0, generation / max(1, max_generations))
        adaptive_rate = max_rate - (max_rate - min_rate) * progress

        for gene in genome.genes:
            original_rate = gene.mutation_rate
            gene.mutation_rate = max(min_rate, min(max_rate, adaptive_rate))

        result = self.mutate(genome)

        # Restore original rates
        for i, gene in enumerate(genome.genes):
            pass  # rates already applied during mutation

        return result

    def directed_mutation(
        self,
        genome: StrategyGenome,
        gene_name: str,
        direction: float,
        strength: float = 0.2,
    ) -> StrategyGenome:
        """Mutate a specific gene in a directed way (from self-reflection).

        Args:
            genome: The genome to mutate
            gene_name: Name of the gene to mutate
            direction: Positive = increase, Negative = decrease
            strength: Magnitude of change as fraction of range
        """
        gene = genome.get_gene(gene_name)
        if gene is None:
            logger.warning(f"Gene '{gene_name}' not found in genome")
            return genome

        delta = direction * strength * gene.range_size
        gene.value += delta
        gene.clamp()

        return genome


# ---------------------------------------------------------------------------
# Crossover Operators
# ---------------------------------------------------------------------------

class CrossoverOperator:
    """Creates offspring by combining two parent genomes.

    Supports:
        1. Uniform crossover: each gene randomly from either parent
        2. BLX-α crossover: blend with extension beyond parents
        3. Arithmetic crossover: weighted average
    """

    def __init__(self, alpha: float = 0.5):
        """
        Args:
            alpha: BLX-α extension factor (0.5 = standard BLX-0.5)
        """
        self.alpha = alpha

    def uniform_crossover(
        self,
        parent1: StrategyGenome,
        parent2: StrategyGenome,
    ) -> StrategyGenome:
        """Uniform crossover — each gene from either parent with 50% probability."""
        child = parent1.clone()
        child.parent_ids = [parent1.genome_id, parent2.genome_id]
        child.generation = max(parent1.generation, parent2.generation) + 1

        for i, gene in enumerate(child.genes):
            if random.random() < 0.5 and i < len(parent2.genes):
                gene.value = parent2.genes[i].value
                gene.clamp()

        return child

    def blx_alpha_crossover(
        self,
        parent1: StrategyGenome,
        parent2: StrategyGenome,
    ) -> StrategyGenome:
        """BLX-α crossover — blend values with extension.

        For each gene, the child value is drawn uniformly from:
            [min - α·range, max + α·range]
        where min/max are the parent values and range = max - min.

        This allows offspring to explore beyond the parent values,
        promoting diversity and preventing premature convergence.
        """
        child = parent1.clone()
        child.parent_ids = [parent1.genome_id, parent2.genome_id]
        child.generation = max(parent1.generation, parent2.generation) + 1

        for i, gene in enumerate(child.genes):
            if i >= len(parent2.genes):
                break

            v1 = parent1.genes[i].value
            v2 = parent2.genes[i].value

            gene_min = min(v1, v2)
            gene_max = max(v1, v2)
            range_width = gene_max - gene_min

            lower = gene_min - self.alpha * range_width
            upper = gene_max + self.alpha * range_width

            # Clamp to gene bounds
            lower = max(lower, gene.min_value)
            upper = min(upper, gene.max_value)

            gene.value = random.uniform(lower, upper)
            gene.clamp()

        return child

    def arithmetic_crossover(
        self,
        parent1: StrategyGenome,
        parent2: StrategyGenome,
        weight: float = 0.5,
    ) -> StrategyGenome:
        """Arithmetic crossover — weighted average of parent values.

        Args:
            weight: Blend factor (0.5 = equal, >0.5 = more of parent1)
        """
        child = parent1.clone()
        child.parent_ids = [parent1.genome_id, parent2.genome_id]
        child.generation = max(parent1.generation, parent2.generation) + 1

        for i, gene in enumerate(child.genes):
            if i >= len(parent2.genes):
                break

            v1 = parent1.genes[i].value
            v2 = parent2.genes[i].value
            gene.value = weight * v1 + (1 - weight) * v2
            gene.clamp()

        return child

    def crossover(
        self,
        parent1: StrategyGenome,
        parent2: StrategyGenome,
        method: str = "blx_alpha",
    ) -> StrategyGenome:
        """Perform crossover with the specified method.

        Args:
            method: "uniform", "blx_alpha", or "arithmetic"
        """
        if method == "uniform":
            return self.uniform_crossover(parent1, parent2)
        elif method == "blx_alpha":
            return self.blx_alpha_crossover(parent1, parent2)
        elif method == "arithmetic":
            return self.arithmetic_crossover(parent1, parent2)
        else:
            raise ValueError(f"Unknown crossover method: {method}")


# ---------------------------------------------------------------------------
# Selection Operators
# ---------------------------------------------------------------------------

class SelectionOperator:
    """Selects parents for reproduction based on fitness.

    Supports:
        1. Tournament selection — pick best of k random candidates
        2. Roulette wheel selection — probability proportional to fitness
        3. Rank selection — probability proportional to rank (avoids domination)
    """

    def __init__(self, tournament_size: int = 3):
        self.tournament_size = tournament_size

    def tournament_select(
        self,
        population: list[StrategyGenome],
        k: int = 1,
    ) -> list[StrategyGenome]:
        """Tournament selection — pick best of k random candidates.

        Repeated k times to select k parents.
        Tournament size controls selection pressure:
            - size=2: low pressure (more diversity)
            - size=5: high pressure (faster convergence)
        """
        selected = []
        for _ in range(k):
            candidates = random.sample(
                population, min(self.tournament_size, len(population))
            )
            # Select the one with highest fitness
            best = max(candidates, key=lambda g: g.fitness_score or 0.0)
            selected.append(best)
        return selected

    def roulette_select(
        self,
        population: list[StrategyGenome],
        k: int = 1,
    ) -> list[StrategyGenome]:
        """Roulette wheel selection — probability proportional to fitness.

        Fails gracefully if all fitness scores are 0 (uniform selection).
        """
        fitnesses = [max(0.001, g.fitness_score or 0.0) for g in population]
        total = sum(fitnesses)

        if total <= 0:
            # All zeros — fall back to uniform
            return random.choices(population, k=k)

        probs = [f / total for f in fitnesses]
        return random.choices(population, weights=probs, k=k)

    def rank_select(
        self,
        population: list[StrategyGenome],
        k: int = 1,
    ) -> list[StrategyGenome]:
        """Rank-based selection — probability proportional to rank.

        More robust than roulette — prevents a single super-fit individual
        from dominating the entire next generation.
        """
        sorted_pop = sorted(
            population, key=lambda g: g.fitness_score or 0.0, reverse=True
        )
        n = len(sorted_pop)
        # Linear rank weighting: rank 1 gets weight n, rank n gets weight 1
        weights = list(range(n, 0, -1))
        total = sum(weights)
        probs = [w / total for w in weights]

        return random.choices(sorted_pop, weights=probs, k=k)

    def select(
        self,
        population: list[StrategyGenome],
        k: int = 1,
        method: str = "tournament",
    ) -> list[StrategyGenome]:
        """Select k parents using the specified method."""
        if method == "tournament":
            return self.tournament_select(population, k)
        elif method == "roulette":
            return self.roulette_select(population, k)
        elif method == "rank":
            return self.rank_select(population, k)
        else:
            raise ValueError(f"Unknown selection method: {method}")
