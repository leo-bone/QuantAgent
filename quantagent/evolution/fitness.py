"""
Fitness Evaluator — Scores strategy genomes via backtesting.

The fitness function is multi-objective, combining:
    1. Risk-adjusted return (Sharpe Ratio) — primary
    2. Capital preservation (1 - Max Drawdown) — penalty
    3. Consistency (Win Rate × Profit Factor) — stability
    4. Trade frequency penalty — avoids over/under-trading

Formula:
    fitness = sharpe × consistency × drawdown_penalty × frequency_factor

Where:
    sharpe          = max(0, portfolio_sharpe_ratio)
    consistency     = win_rate × min(profit_factor, 3.0)
    drawdown_penalty= max(0, 1 - max_drawdown)
    frequency_factor= penalty if trades < min_trades or > max_trades
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from quantagent.evolution.genome import StrategyGenome, GenomeDecoder

logger = logging.getLogger(__name__)


@dataclass
class FitnessResult:
    """Complete fitness evaluation result."""
    genome_id: str
    generation: int

    # Raw backtest metrics
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_trade_pnl: float = 0.0
    calmar_ratio: float = 0.0

    # Computed fitness
    fitness_score: float = 0.0
    fitness_breakdown: dict = field(default_factory=dict)

    # Meta
    evaluation_error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Whether this result is usable (no errors, has trades)."""
        return self.evaluation_error is None and self.total_trades > 0


class FitnessEvaluator:
    """Evaluates the fitness of a StrategyGenome by running backtests.

    The evaluator decodes the genome into trading parameters, constructs
    a backtest configuration, runs the backtest, and computes the fitness.
    """

    def __init__(
        self,
        # Fitness weights
        sharpe_weight: float = 1.0,
        consistency_weight: float = 0.6,
        drawdown_weight: float = 0.8,
        frequency_weight: float = 0.3,
        # Trade frequency bounds
        min_trades: int = 5,
        max_trades: int = 200,
        # Risk-free rate for Sharpe
        risk_free_rate: float = 0.0,
    ):
        self.sharpe_weight = sharpe_weight
        self.consistency_weight = consistency_weight
        self.drawdown_weight = drawdown_weight
        self.frequency_weight = frequency_weight
        self.min_trades = min_trades
        self.max_trades = max_trades
        self.risk_free_rate = risk_free_rate

    def compute_fitness(self, result: FitnessResult) -> float:
        """Compute fitness score from backtest metrics.

        Multi-objective fitness with the following components:
            1. Sharpe component: rewards risk-adjusted returns
            2. Consistency component: rewards stable profitability
            3. Drawdown penalty: penalizes large drawdowns
            4. Frequency factor: penalizes too few/many trades
        """
        if not result.is_valid:
            return 0.0

        # 1. Sharpe component — primary driver
        sharpe = max(0.0, result.sharpe_ratio)
        sharpe_component = sharpe * self.sharpe_weight

        # 2. Consistency component — win_rate × profit_factor
        profit_factor_capped = min(result.profit_factor, 5.0) if result.profit_factor > 0 else 0.0
        consistency = result.win_rate * profit_factor_capped
        consistency_component = consistency * self.consistency_weight

        # 3. Drawdown penalty — exponential decay
        drawdown_penalty = max(0.0, 1.0 - result.max_drawdown) ** 1.5
        drawdown_component = drawdown_penalty * self.drawdown_weight

        # 4. Frequency factor — penalize extremes
        if result.total_trades < self.min_trades:
            freq_factor = result.total_trades / self.min_trades
        elif result.total_trades > self.max_trades:
            freq_factor = self.max_trades / result.total_trades
        else:
            freq_factor = 1.0
        frequency_component = freq_factor * self.frequency_weight

        # Weighted sum
        fitness = (
            sharpe_component
            + consistency_component
            + drawdown_component
            + frequency_component
        )

        # Store breakdown for transparency
        result.fitness_breakdown = {
            "sharpe_component": round(sharpe_component, 4),
            "consistency_component": round(consistency_component, 4),
            "drawdown_component": round(drawdown_component, 4),
            "frequency_component": round(frequency_component, 4),
            "raw_sharpe": round(sharpe, 4),
            "raw_consistency": round(consistency, 4),
            "raw_drawdown_penalty": round(drawdown_penalty, 4),
            "raw_freq_factor": round(freq_factor, 4),
        }

        return round(max(0.0, fitness), 6)

    async def evaluate(
        self,
        genome: StrategyGenome,
        token: str = "SOL",
        chain: str = "solana",
        days: int = 30,
        initial_capital: float = 10000.0,
    ) -> FitnessResult:
        """Evaluate a genome by running a backtest and computing fitness.

        Args:
            genome: The strategy genome to evaluate
            token: Token to backtest on
            chain: Chain to backtest on
            days: Number of days of historical data
            initial_capital: Starting capital

        Returns:
            FitnessResult with backtest metrics and computed fitness
        """
        result = FitnessResult(
            genome_id=genome.genome_id,
            generation=genome.generation,
        )

        try:
            # Decode genome into parameters
            params = GenomeDecoder.decode_all(genome)

            # Run backtest with decoded parameters
            from quantagent.backtest.runner import run_backtest_with_params
            from quantagent.common.models import Chain as ChainEnum

            chain_enum = ChainEnum.SOLANA if chain == "solana" else ChainEnum.BSC

            backtest_result = await run_backtest_with_params(
                token=token,
                chain=chain_enum,
                days=days,
                initial_capital=initial_capital,
                params=params,
            )

            # Extract metrics
            result.total_return = backtest_result.total_return_pct / 100.0
            result.sharpe_ratio = backtest_result.sharpe_ratio
            result.sortino_ratio = backtest_result.sortino_ratio
            result.max_drawdown = backtest_result.max_drawdown_pct / 100.0
            result.win_rate = backtest_result.win_rate
            result.profit_factor = backtest_result.profit_factor
            result.total_trades = backtest_result.total_trades
            result.avg_trade_pnl = backtest_result.avg_trade_pnl
            result.calmar_ratio = backtest_result.calmar_ratio

        except Exception as e:
            logger.warning(f"Backtest failed for genome {genome.genome_id}: {e}")
            result.evaluation_error = str(e)
            result.fitness_score = 0.0
            return result

        # Compute fitness
        result.fitness_score = self.compute_fitness(result)
        genome.fitness_score = result.fitness_score
        genome.fitness_result = result

        logger.info(
            f"Genome {genome.genome_id} (gen {genome.generation}): "
            f"fitness={result.fitness_score:.4f} "
            f"sharpe={result.sharpe_ratio:.2f} "
            f"win_rate={result.win_rate:.2%} "
            f"max_dd={result.max_drawdown:.2%} "
            f"trades={result.total_trades}"
        )

        return result

    async def evaluate_population(
        self,
        population: list[StrategyGenome],
        token: str = "SOL",
        chain: str = "solana",
        days: int = 30,
        initial_capital: float = 10000.0,
    ) -> list[FitnessResult]:
        """Evaluate an entire population of genomes."""
        results = []
        for genome in population:
            result = await self.evaluate(
                genome, token=token, chain=chain, days=days,
                initial_capital=initial_capital,
            )
            results.append(result)
        return results
