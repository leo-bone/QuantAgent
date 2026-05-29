"""
Evolution Engine — Main evolutionary loop.

Orchestrates the complete self-evolution cycle:
    1. Initialize population (default + random genomes)
    2. Evaluate fitness (backtest each genome)
    3. Select parents (tournament/rank)
    4. Apply genetic operators (crossover + mutation)
    5. Validate offspring (backtest)
    6. Elitism (preserve top performers)
    7. Convergence detection
    8. Auto-deploy best genome to live trading

The engine runs asynchronously and can be triggered:
    - Manually via API
    - Periodically on a schedule
    - When performance degrades (triggered by metrics)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from quantagent.evolution.genome import (
    StrategyGenome,
    GenomeFactory,
    GenomeDecoder,
)
from quantagent.evolution.fitness import FitnessEvaluator, FitnessResult
from quantagent.evolution.operators import (
    MutationOperator,
    CrossoverOperator,
    SelectionOperator,
)

logger = logging.getLogger(__name__)

# Default storage path for evolution history
EVOLUTION_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "evolution"


@dataclass
class EvolutionConfig:
    """Configuration for the evolution engine."""
    # Population
    population_size: int = 20
    elite_count: int = 2          # Number of top genomes preserved unchanged
    offspring_count: int = 16     # Number of new genomes per generation

    # Genetic operators
    crossover_method: str = "blx_alpha"  # uniform, blx_alpha, arithmetic
    selection_method: str = "tournament" # tournament, roulette, rank
    tournament_size: int = 3

    # Mutation
    default_mutation_rate: float = 0.3
    boundary_probability: float = 0.1
    adaptive_mutation: bool = True  # Decrease mutation rate over generations

    # Convergence
    max_generations: int = 30
    convergence_threshold: float = 0.001  # Stop if improvement < this
    patience: int = 5                     # Generations without improvement

    # Backtest
    backtest_token: str = "SOL"
    backtest_chain: str = "solana"
    backtest_days: int = 30
    initial_capital: float = 10000.0

    # Auto-deploy
    auto_deploy: bool = True
    deploy_min_fitness: float = 0.5  # Minimum fitness to auto-deploy


@dataclass
class EvolutionResult:
    """Result of a complete evolution run."""
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    generations_run: int = 0
    total_genomes_evaluated: int = 0
    best_genome: Optional[StrategyGenome] = None
    best_fitness: float = 0.0
    fitness_history: list[float] = field(default_factory=list)
    convergence_reason: Optional[str] = None
    deployed: bool = False

    # Generation-level stats
    generation_stats: list[dict] = field(default_factory=list)

    def to_summary(self) -> dict:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "generations_run": self.generations_run,
            "total_genomes_evaluated": self.total_genomes_evaluated,
            "best_fitness": round(self.best_fitness, 6),
            "convergence_reason": self.convergence_reason,
            "deployed": self.deployed,
            "best_genome": self.best_genome.summary() if self.best_genome else None,
            "fitness_history": [round(f, 4) for f in self.fitness_history],
        }


class EvolutionEngine:
    """Main evolutionary optimization engine.

    Usage:
        engine = EvolutionEngine(config=EvolutionConfig())
        result = await engine.evolve()
        print(f"Best fitness: {result.best_fitness}")
    """

    def __init__(
        self,
        config: Optional[EvolutionConfig] = None,
        data_dir: Optional[Path] = None,
    ):
        self.config = config or EvolutionConfig()
        self.data_dir = data_dir or EVOLUTION_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Operators
        self.mutator = MutationOperator(
            default_mutation_rate=self.config.default_mutation_rate,
            boundary_probability=self.config.boundary_probability,
        )
        self.crossover = CrossoverOperator(alpha=0.5)
        self.selector = SelectionOperator(
            tournament_size=self.config.tournament_size
        )
        self.evaluator = FitnessEvaluator()

        # State
        self.population: list[StrategyGenome] = []
        self.generation: int = 0
        self.best_ever: Optional[StrategyGenome] = None
        self.best_fitness_ever: float = 0.0
        self.history: list[dict] = []
        self._is_running: bool = False

        # Load previous state if available
        self._load_state()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_status(self) -> dict:
        """Get current evolution status."""
        return {
            "is_running": self._is_running,
            "generation": self.generation,
            "population_size": len(self.population),
            "best_fitness_ever": round(self.best_fitness_ever, 6) if self.best_fitness_ever > 0 else None,
            "best_genome_id": self.best_ever.genome_id if self.best_ever else None,
            "config": {
                "population_size": self.config.population_size,
                "max_generations": self.config.max_generations,
                "crossover_method": self.config.crossover_method,
                "selection_method": self.config.selection_method,
                "auto_deploy": self.config.auto_deploy,
            },
        }

    # -----------------------------------------------------------------------
    # Core Evolution Loop
    # -----------------------------------------------------------------------

    async def evolve(self) -> EvolutionResult:
        """Run the complete evolution process.

        Returns:
            EvolutionResult with best genome and statistics
        """
        if self._is_running:
            logger.warning("Evolution already in progress, skipping")
            return EvolutionResult(convergence_reason="already_running")

        self._is_running = True
        result = EvolutionResult()

        try:
            # Initialize population if empty
            if not self.population:
                self.population = GenomeFactory.create_population(
                    self.config.population_size
                )
                logger.info(f"Initialized population with {len(self.population)} genomes")

            no_improvement_count = 0

            for gen in range(self.config.max_generations):
                self.generation = gen
                gen_start = time.time()

                # Step 1: Evaluate fitness
                logger.info(f"--- Generation {gen} ---")
                fitness_results = await self.evaluator.evaluate_population(
                    self.population,
                    token=self.config.backtest_token,
                    chain=self.config.backtest_chain,
                    days=self.config.backtest_days,
                    initial_capital=self.config.initial_capital,
                )

                result.total_genomes_evaluated += len(self.population)

                # Step 2: Sort by fitness
                self.population.sort(
                    key=lambda g: g.fitness_score or 0.0, reverse=True
                )

                # Step 3: Track best
                gen_best = self.population[0]
                gen_best_fitness = gen_best.fitness_score or 0.0
                result.fitness_history.append(gen_best_fitness)

                if gen_best_fitness > self.best_fitness_ever:
                    self.best_fitness_ever = gen_best_fitness
                    self.best_ever = gen_best.clone()
                    no_improvement_count = 0
                    logger.info(
                        f"  New best! fitness={gen_best_fitness:.4f} "
                        f"genome={gen_best.genome_id}"
                    )
                else:
                    no_improvement_count += 1

                # Step 4: Record generation stats
                gen_stats = {
                    "generation": gen,
                    "best_fitness": round(gen_best_fitness, 4),
                    "avg_fitness": round(
                        sum(g.fitness_score or 0 for g in self.population) / len(self.population), 4
                    ),
                    "worst_fitness": round(
                        min(g.fitness_score or 0 for g in self.population), 4
                    ),
                    "duration_sec": round(time.time() - gen_start, 2),
                }
                result.generation_stats.append(gen_stats)
                self.history.append(gen_stats)

                logger.info(
                    f"  Gen {gen}: best={gen_best_fitness:.4f} "
                    f"avg={gen_stats['avg_fitness']:.4f} "
                    f"worst={gen_stats['worst_fitness']:.4f} "
                    f"duration={gen_stats['duration_sec']:.1f}s"
                )

                # Step 5: Convergence check
                if no_improvement_count >= self.config.patience:
                    result.convergence_reason = f"no_improvement_for_{no_improvement_count}_gens"
                    logger.info(f"  Converged: no improvement for {no_improvement_count} generations")
                    break

                if gen > 0 and abs(gen_best_fitness - result.fitness_history[-2]) < self.config.convergence_threshold:
                    # Very small improvement
                    pass  # Handled by patience counter

                # Step 6: Create next generation
                if gen < self.config.max_generations - 1:
                    self.population = self._create_next_generation(gen)

                # Save state after each generation
                self._save_state()

            # Finalize
            result.generations_run = self.generation + 1
            result.best_genome = self.best_ever
            result.best_fitness = self.best_fitness_ever
            result.end_time = datetime.now(timezone.utc)

            if result.convergence_reason is None:
                result.convergence_reason = "max_generations_reached"

            # Auto-deploy if configured
            if self.config.auto_deploy and self.best_ever and self.best_fitness_ever >= self.config.deploy_min_fitness:
                self._deploy_genome(self.best_ever)
                result.deployed = True

            logger.info(
                f"Evolution complete: {result.generations_run} generations, "
                f"best_fitness={result.best_fitness:.4f}, "
                f"converged={result.convergence_reason}, "
                f"deployed={result.deployed}"
            )

        except Exception as e:
            logger.error(f"Evolution failed: {e}")
            result.convergence_reason = f"error: {str(e)}"
        finally:
            self._is_running = False
            self._save_state()

        return result

    # -----------------------------------------------------------------------
    # Next Generation Creation
    # -----------------------------------------------------------------------

    def _create_next_generation(self, generation: int) -> list[StrategyGenome]:
        """Create the next generation from the current population."""
        next_gen: list[StrategyGenome] = []

        # 1. Elitism — preserve top performers unchanged
        elite = self.population[:self.config.elite_count]
        for e in elite:
            elite_clone = e.clone()
            elite_clone.generation = generation + 1
            elite_clone.parent_ids = [e.genome_id]
            elite_clone.fitness_score = e.fitness_score  # Preserve fitness
            next_gen.append(elite_clone)

        # 2. Fill rest with offspring
        while len(next_gen) < self.config.population_size:
            # Select parents
            parents = self.selector.select(
                self.population, k=2, method=self.config.selection_method
            )

            # Crossover
            child = self.crossover.crossover(
                parents[0], parents[1], method=self.config.crossover_method
            )
            child.generation = generation + 1

            # Mutation
            if self.config.adaptive_mutation:
                self.mutator.mutate_with_adaptive_rate(
                    child, generation, self.config.max_generations
                )
            else:
                self.mutator.mutate(child)

            next_gen.append(child)

        return next_gen[:self.config.population_size]

    # -----------------------------------------------------------------------
    # Deployment
    # -----------------------------------------------------------------------

    def _deploy_genome(self, genome: StrategyGenome) -> None:
        """Deploy the best genome as the active strategy.

        Saves the genome to the active strategy file, which the
        QuantAgent picks up on its next cycle.
        """
        active_path = self.data_dir / "active_genome.json"
        genome_dict = genome.to_dict()
        genome_dict["deployed_at"] = datetime.now(timezone.utc).isoformat()

        with open(active_path, "w") as f:
            json.dump(genome_dict, f, indent=2)

        logger.info(
            f"Deployed genome {genome.genome_id} (fitness={genome.fitness_score:.4f}) "
            f"to {active_path}"
        )

    @classmethod
    def load_active_genome(cls, data_dir: Optional[Path] = None) -> Optional[StrategyGenome]:
        """Load the currently deployed active genome."""
        dir_path = data_dir or EVOLUTION_DATA_DIR
        active_path = dir_path / "active_genome.json"

        if not active_path.exists():
            return None

        try:
            with open(active_path) as f:
                data = json.load(f)
            return StrategyGenome.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load active genome: {e}")
            return None

    # -----------------------------------------------------------------------
    # State Persistence
    # -----------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save evolution state to disk."""
        state = {
            "generation": self.generation,
            "best_fitness_ever": self.best_fitness_ever,
            "best_genome_id": self.best_ever.genome_id if self.best_ever else None,
            "population": [g.to_dict() for g in self.population],
            "history": self.history[-100:],  # Keep last 100 generations
        }

        state_path = self.data_dir / "evolution_state.json"
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self) -> None:
        """Load evolution state from disk if available."""
        state_path = self.data_dir / "evolution_state.json"
        if not state_path.exists():
            return

        try:
            with open(state_path) as f:
                state = json.load(f)

            self.generation = state.get("generation", 0)
            self.best_fitness_ever = state.get("best_fitness_ever", 0.0)
            self.history = state.get("history", [])

            # Restore population
            pop_data = state.get("population", [])
            if pop_data:
                self.population = [StrategyGenome.from_dict(d) for d in pop_data]

            # Restore best genome
            best_id = state.get("best_genome_id")
            if best_id and self.population:
                for g in self.population:
                    if g.genome_id == best_id:
                        self.best_ever = g
                        break

            logger.info(
                f"Loaded evolution state: gen {self.generation}, "
                f"population {len(self.population)}, "
                f"best_fitness {self.best_fitness_ever:.4f}"
            )
        except Exception as e:
            logger.warning(f"Failed to load evolution state: {e}")
