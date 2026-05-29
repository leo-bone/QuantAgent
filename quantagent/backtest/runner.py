"""Backtest runner — convenience wrapper for running backtests.

Provides a high-level API to run backtests with data fetching,
signal generation, and result visualization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from quantagent.common.models import Chain, OHLCV
from quantagent.backtest.engine import BacktestEngine, BacktestResult


class BacktestRunner:
    """High-level backtest runner that handles data fetching and execution.

    Usage:
        runner = BacktestRunner()
        result = await runner.run(
            token="SOL",
            chain=Chain.SOLANA,
            days=90,
        )
        print(result.to_summary())
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        position_size_pct: float = 0.10,
        stop_loss_pct: float = 0.05,
        take_profit_ratio: float = 2.0,
    ):
        self.engine = BacktestEngine(
            initial_capital=initial_capital,
            position_size_pct=position_size_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_ratio=take_profit_ratio,
        )

    async def run(
        self,
        token: str,
        chain: Chain,
        days: int = 90,
        interval: str = "1h",
        signal_generator=None,
        strategy=None,
    ) -> BacktestResult:
        """Run a backtest for a given token and chain.

        Args:
            token: Token symbol (e.g., "SOL", "BNB")
            chain: Chain to test on
            days: Number of days to backtest
            interval: Candle interval ("1h", "4h", "1d")
            signal_generator: Optional custom signal generator
            strategy: Optional custom strategy

        Returns:
            BacktestResult with full performance metrics
        """
        # Fetch historical data
        ohlcv_data = await self._fetch_historical_data(token, chain, days, interval)

        if not ohlcv_data:
            logger.warning(f"No historical data available for {token} on {chain.value}")
            return BacktestResult(
                token=token,
                chain=chain,
                start_date=datetime.now(timezone.utc),
                end_date=datetime.now(timezone.utc),
                initial_capital=self.engine.initial_capital,
            )

        logger.info(
            f"[Backtest] Running {token} on {chain.value}: "
            f"{len(ohlcv_data)} candles, {days} days, {interval} interval"
        )

        # Use provided signal generator or default technical
        if signal_generator is None:
            signal_generator = self._create_default_signal_generator()

        if strategy is None:
            strategy = self._create_default_strategy()

        # Run backtest
        result = self.engine.run(
            ohlcv_data=ohlcv_data,
            signal_generator=signal_generator,
            strategy=strategy,
            token=token,
            chain=chain,
        )

        # Log summary
        summary = result.to_summary()
        logger.info(
            f"[Backtest] {token} results: "
            f"Return={summary['total_return_pct']:.1f}%, "
            f"Sharpe={summary['sharpe_ratio']:.2f}, "
            f"MaxDD={summary['max_drawdown_pct']:.1f}%, "
            f"WinRate={summary['win_rate']:.1%}, "
            f"Trades={summary['total_trades']}"
        )

        return result

    async def _fetch_historical_data(
        self,
        token: str,
        chain: Chain,
        days: int,
        interval: str,
    ) -> list[OHLCV]:
        """Fetch historical OHLCV data from chain providers."""
        try:
            if chain == Chain.SOLANA:
                from quantagent.data.solana_collector import SolanaProvider
                from config.settings import settings
                provider = SolanaProvider(settings.solana_rpc_url)
                ohlcv = await provider.get_ohlcv(token, interval=interval, limit=days * 24 if interval == "1h" else days * 6 if interval == "4h" else days)
                await provider.close()
                return ohlcv
            elif chain == Chain.BSC:
                from quantagent.data.bsc_collector import BSCProvider
                from config.settings import settings
                provider = BSCProvider(settings.bsc_rpc_url)
                ohlcv = await provider.get_ohlcv(token, interval=interval, limit=days * 24 if interval == "1h" else days * 6 if interval == "4h" else days)
                await provider.close()
                return ohlcv
        except Exception as e:
            logger.error(f"Failed to fetch historical data for backtest: {e}")
            return []

    def _create_default_signal_generator(self):
        """Create default technical signal generator for backtesting."""
        from quantagent.signal.technical import TechnicalSignalGenerator
        return TechnicalSignalGenerator()

    def _create_default_strategy(self):
        """Create default momentum strategy for backtesting."""
        from quantagent.strategy.momentum import MomentumStrategy
        return MomentumStrategy()


async def run_backtest(
    token: str = "SOL",
    chain: Chain = Chain.SOLANA,
    days: int = 90,
    initial_capital: float = 10000.0,
) -> BacktestResult:
    """Convenience function to run a backtest.

    Usage:
        result = await run_backtest("SOL", Chain.SOLANA, days=90)
    """
    runner = BacktestRunner(initial_capital=initial_capital)
    return await runner.run(token=token, chain=chain, days=days)


async def run_backtest_with_params(
    token: str = "SOL",
    chain: Chain = Chain.SOLANA,
    days: int = 30,
    initial_capital: float = 10000.0,
    params: Optional[dict] = None,
) -> BacktestResult:
    """Run a backtest with custom parameters (for evolution engine).

    Args:
        token: Token symbol
        chain: Blockchain
        days: Number of days to backtest
        initial_capital: Starting capital
        params: Decoded genome parameters dict containing:
            - signal_weights: dict of source -> weight
            - technical_params: dict of indicator parameters
            - risk_params: dict of risk management parameters
            - entry_rules: dict of entry conditions
            - exit_rules: dict of exit conditions

    Returns:
        BacktestResult with full performance metrics
    """
    params = params or {}

    # Extract risk params for engine setup
    risk_params = params.get("risk_params", {})
    runner = BacktestRunner(
        initial_capital=initial_capital,
        position_size_pct=risk_params.get("max_position_pct", 0.10),
        stop_loss_pct=risk_params.get("stop_loss_pct", 0.05),
        take_profit_ratio=risk_params.get("take_profit_ratio", 2.0),
    )

    # Create signal generator with custom technical params
    tech_params = params.get("technical_params", {})
    signal_gen = _create_parametrized_signal_generator(tech_params)

    # Create strategy with custom entry rules
    entry_rules = params.get("entry_rules", {})
    strategy = _create_parametrized_strategy(entry_rules, risk_params)

    return await runner.run(
        token=token,
        chain=chain,
        days=days,
        signal_generator=signal_gen,
        strategy=strategy,
    )


def _create_parametrized_signal_generator(tech_params: dict):
    """Create a TechnicalSignalGenerator with custom indicator parameters."""
    from quantagent.signal.technical import TechnicalSignalGenerator

    gen = TechnicalSignalGenerator()

    # Override default parameters if provided
    if tech_params.get("rsi_period"):
        gen.rsi_period = int(tech_params["rsi_period"])
    if tech_params.get("rsi_overbought"):
        gen.rsi_overbought = tech_params["rsi_overbought"]
    if tech_params.get("rsi_oversold"):
        gen.rsi_oversold = tech_params["rsi_oversold"]
    if tech_params.get("macd_fast"):
        gen.macd_fast = int(tech_params["macd_fast"])
    if tech_params.get("macd_slow"):
        gen.macd_slow = int(tech_params["macd_slow"])
    if tech_params.get("macd_signal"):
        gen.macd_signal = int(tech_params["macd_signal"])
    if tech_params.get("bb_period"):
        gen.bb_period = int(tech_params["bb_period"])
    if tech_params.get("bb_std"):
        gen.bb_std = tech_params["bb_std"]
    if tech_params.get("atr_period"):
        gen.atr_period = int(tech_params["atr_period"])

    return gen


def _create_parametrized_strategy(entry_rules: dict, risk_params: dict):
    """Create a MomentumStrategy with custom parameters."""
    from quantagent.strategy.momentum import MomentumStrategy

    strategy = MomentumStrategy()

    # Override entry rules
    if entry_rules.get("min_confidence"):
        strategy.min_confidence = entry_rules["min_confidence"]
    if entry_rules.get("bullish_threshold"):
        strategy.bullish_threshold = entry_rules["bullish_threshold"]

    # Override risk params
    if risk_params.get("max_position_pct"):
        strategy.max_position_pct = risk_params["max_position_pct"]
    if risk_params.get("stop_loss_pct"):
        strategy.default_stop_loss_pct = risk_params["stop_loss_pct"]
    if risk_params.get("take_profit_ratio"):
        strategy.take_profit_ratio = risk_params["take_profit_ratio"]

    return strategy
