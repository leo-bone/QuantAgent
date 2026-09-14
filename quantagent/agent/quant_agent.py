"""QuantAgent main agent - the brain that ties everything together.

The QuantAgent is an autonomous trading agent that:
1. Monitors market data across chains
2. Generates signals using technical, on-chain, and LLM analysis
3. Evaluates strategies and makes trading decisions
4. Executes trades with risk management
5. Learns from outcomes and adapts

State machine: IDLE → MONITORING → SIGNAL_DETECTED → EVALUATING → EXECUTING → POST_TRADE → IDLE
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

from quantagent.common.models import (
    AgentConfig,
    AgentState,
    AgentStatus,
    Chain,
    CombinedSignal,
    Order,
    OrderType,
    RiskAssessment,
    StrategyAction,
    TradeStatus,
)
from quantagent.data.dex_collector import DexCollector
from quantagent.data.ohlcv_provider import fetch_ohlcv
from quantagent.data.whale_monitor import WhaleMonitor
from quantagent.data.database import Database
from quantagent.signal.combiner import SignalCombiner
from quantagent.signal.technical import TechnicalSignalGenerator
from quantagent.signal.llm_analyzer import LLMAnalyzer
from quantagent.strategy.base import BaseStrategy
from quantagent.strategy.momentum import MomentumStrategy
from quantagent.strategy.risk_manager import RiskManager
from quantagent.agent.memory import AgentMemoryStore
from quantagent.execution.executor import ChainExecutor, ExecutionResult


class QuantAgent:
    """The main quantitative trading agent.

    Orchestrates the full pipeline:
        Data Collection → Signal Generation → Strategy Evaluation → Risk Check → Execution
    """

    # Token → supported chains mapping (skip unsupported combos)
    TOKEN_CHAIN_MAP: dict[str, list[Chain]] = {
        "SOL": [Chain.SOLANA],
        "BNB": [Chain.BSC],
        "ETH": [Chain.SOLANA, Chain.BSC],   # wrapped ETH on both
        "BTC": [Chain.SOLANA, Chain.BSC],   # wrapped BTC on both
        "USDC": [Chain.SOLANA, Chain.BSC],
        "USDT": [Chain.SOLANA, Chain.BSC],
        "CAKE": [Chain.BSC],
        "JUP": [Chain.SOLANA],
        "BONK": [Chain.SOLANA],
        "WIF": [Chain.SOLANA],
    }

    def __init__(
        self,
        config: AgentConfig,
        dex_collector: DexCollector,
        strategy: BaseStrategy | None = None,
        risk_manager: RiskManager | None = None,
        signal_combiner: SignalCombiner | None = None,
        llm_api_key: str = "",
        birdeye_api_key: str = "",
        executors: dict[Chain, ChainExecutor] | None = None,
        wallet_address: str = "",
        private_key: str = "",
        bsc_private_key: str = "",
        database: Database | None = None,
    ):
        self.config = config
        self.dex_collector = dex_collector
        self.strategy = strategy or MomentumStrategy()
        self.risk_manager = risk_manager or RiskManager(
            max_position_size_usd=config.max_position_size_usd,
            max_daily_loss_usd=config.max_daily_loss_usd,
        )
        self.signal_combiner = signal_combiner or SignalCombiner()

        # Signal generators
        self.technical_gen = TechnicalSignalGenerator()
        self.llm_gen = LLMAnalyzer(openai_api_key=llm_api_key)

        # Whale monitor for on-chain signals
        self.whale_monitor = WhaleMonitor(dex_collector)

        # Execution layer (chain → executor)
        self.executors: dict[Chain, ChainExecutor] = executors or {}

        # Credentials (only used in live mode, never logged)
        self._wallet_address = wallet_address
        self._private_key = private_key
        self._bsc_private_key = bsc_private_key

        # API keys
        self._birdeye_api_key = birdeye_api_key

        # Database (persistence)
        self.db = database

        # State
        self.state = AgentState.IDLE
        self.agent_id = str(uuid.uuid4())[:8]
        self.start_time = datetime.now(timezone.utc)

        # Memory
        self.memory = AgentMemoryStore()

        # Trading state
        self._active_orders: list[Order] = []
        self._trade_history: list[dict] = []
        self._execution_history: list[ExecutionResult] = []
        self._portfolio_usd: float = 0.0
        self._cash_usd: float = 0.0
        # position ledger: (token, chain) -> {"units": float, "cost_usd": float}
        self._ledger: dict = {}
        self._last_prices: dict = {}  # (token, chain) -> last seen price
        self._kill_switch_active: bool = False
        self._running = False

    def register_executor(self, chain: Chain, executor: ChainExecutor) -> None:
        """Register an executor for a specific chain."""
        self.executors[chain] = executor
        logger.info(f"Registered {chain.value} executor (paper={executor.paper_mode})")

    @property
    def status(self) -> AgentStatus:
        """Get current agent status."""
        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        total_trades = len(self._trade_history)
        wins = sum(1 for t in self._trade_history if t.get("pnl_usd", 0) > 0)
        total_pnl = sum(t.get("pnl_usd", 0) for t in self._trade_history)

        return AgentStatus(
            name=f"QuantAgent-{self.agent_id}",
            state=self.state,
            uptime_seconds=uptime,
            total_trades=total_trades,
            total_pnl_usd=total_pnl,
            win_rate=wins / total_trades if total_trades > 0 else 0.0,
            active_positions=len(self._active_orders),
            last_signal=None,
            last_trade=None,
            errors_count=0,
        )

    async def run(self, poll_interval: int = 60) -> None:
        """Main agent loop.

        Args:
            poll_interval: Seconds between monitoring cycles
        """
        self._running = True
        self.state = AgentState.MONITORING

        # Initialize database if provided
        if self.db:
            try:
                await self.db.init()
                logger.info("Database initialized for agent persistence")
            except Exception as e:
                logger.warning(f"Database init failed, running in-memory only: {e}")

        logger.info(f"QuantAgent-{self.agent_id} started in {self.config.trading_mode} mode")
        logger.info(f"Monitoring: {self.config.tokens_to_monitor} on {[c.value for c in self.config.chains]}")

        try:
            while self._running:
                try:
                    await self._monitoring_cycle()
                except Exception as e:
                    logger.error(f"Agent cycle error: {e}")
                    self.state = AgentState.ERROR

                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            logger.info("Agent stopped by cancellation")
        finally:
            self.state = AgentState.IDLE
            # Save final state to database
            if self.db:
                try:
                    summary = self.get_portfolio_summary()
                    await self.db.save_agent_state(
                        self.agent_id,
                        {
                            "state": "idle",
                            "portfolio_usd": summary["portfolio_usd"],
                            "total_trades": summary["total_trades"],
                            "total_pnl": sum(t.get("pnl_usd", 0) for t in self._trade_history),
                            "win_rate": self.status.win_rate,
                            "config": self.config.model_dump(),
                        },
                    )
                    await self.db.close()
                except Exception as e:
                    logger.warning(f"Failed to save final state: {e}")
            logger.info(f"QuantAgent-{self.agent_id} shutdown")

    async def _monitoring_cycle(self) -> None:
        """One complete monitoring cycle with real data flows."""
        self.state = AgentState.MONITORING

        for token in self.config.tokens_to_monitor:
            # Determine which chains this token is supported on
            supported_chains = self.TOKEN_CHAIN_MAP.get(token, self.config.chains)
            for chain in supported_chains:
                # Rate-limit CoinGecko calls: free tier ~10-30 req/min
                await asyncio.sleep(1.2)
                try:
                    # ─── Phase 1: Collect Market Data ───
                    price = await self.dex_collector.get_price(token, chain)
                    fear_greed = await self.dex_collector.get_fear_greed_index()

                    logger.debug(
                        f"[{token}/{chain.value}] Price: ${price:.4f} | "
                        f"F&G: {fear_greed.get('value', 'N/A')}"
                    )

                    # ─── Phase 2: Fetch OHLCV & Generate Technical Signals ───
                    self.state = AgentState.SIGNAL_DETECTED
                    technical_signals = []

                    try:
                        candles = await fetch_ohlcv(
                            token=token,
                            chain=chain.value,
                            interval="1h",
                            limit=100,
                            birdeye_api_key=self._birdeye_api_key,
                        )

                        if candles and len(candles) >= 30:
                            # Convert OHLCV models to DataFrame
                            df = pd.DataFrame([{
                                "open": c.open,
                                "high": c.high,
                                "low": c.low,
                                "close": c.close,
                                "volume": c.volume,
                            } for c in candles])

                            technical_signals = await self.technical_gen.generate_signals(
                                token=token, chain=chain, df=df,
                            )
                            logger.info(
                                f"[{token}] Technical signals: {len(technical_signals)} "
                                f"from {len(candles)} candles"
                            )
                        else:
                            logger.warning(
                                f"[{token}] Insufficient OHLCV data "
                                f"({len(candles) if candles else 0} candles, need ≥30)"
                            )
                    except Exception as e:
                        logger.warning(f"[{token}] OHLCV/technical analysis failed: {e}")

                    # ─── Phase 3: On-chain Signals (Whale Monitor) ───
                    onchain_signals = []
                    try:
                        onchain_signals = await self.whale_monitor.check_large_transfers(
                            token=token, chain=chain,
                        )
                        if onchain_signals:
                            logger.info(f"[{token}] On-chain signals: {len(onchain_signals)}")
                    except Exception as e:
                        logger.debug(f"[{token}] Whale monitor unavailable: {e}")

                    # ─── Phase 4: LLM Signal ───
                    llm_signal = None
                    if await self.llm_gen.is_available():
                        # Build technical summary for LLM context
                        tech_summary = self._summarize_technical_signals(technical_signals)
                        market_context = {
                            "price_usd": price,
                            "fear_greed_index": fear_greed.get("value", 50),
                            "technical_summary": tech_summary,
                            "onchain_events": (
                                "; ".join(s.description for s in onchain_signals)
                                if onchain_signals else "No notable on-chain events"
                            ),
                        }
                        try:
                            llm_signals = await self.llm_gen.generate_signals(
                                token, chain, market_context,
                            )
                            if llm_signals:
                                llm_signal = llm_signals[0]
                        except Exception as e:
                            logger.warning(f"[{token}] LLM analysis failed: {e}")

                    # ─── Phase 5: Combine Signals ───
                    self.state = AgentState.EVALUATING
                    combined = self.signal_combiner.combine(
                        token=token,
                        chain=chain,
                        technical_signals=technical_signals,
                        onchain_signals=onchain_signals,
                        llm_signal=llm_signal,
                    )

                    if combined.signal_type.value == "neutral" or combined.confidence < 0.5:
                        logger.debug(
                            f"[{token}] No actionable signal "
                            f"(dir={combined.signal_type.value}, conf={combined.confidence:.2f})"
                        )
                        continue

                    # ─── Phase 6: Risk Assessment ───
                    # Calculate 24h volatility from OHLCV if available
                    volatility_24h = 0.0
                    if candles and len(candles) >= 2:
                        closes = [c.close for c in candles]
                        if closes[-1] > 0 and closes[-2] > 0:
                            returns = [
                                (closes[i] - closes[i - 1]) / closes[i - 1]
                                for i in range(1, len(closes))
                            ]
                            if returns:
                                import numpy as np
                                volatility_24h = float(np.std(returns)) * (24 ** 0.5)

                    risk = self.risk_manager.assess_risk(
                        signal=combined,
                        portfolio_usd=self._portfolio_usd,
                        volatility_24h=volatility_24h,
                    )

                    # ─── Phase 7: Strategy Evaluation ───
                    action = self.strategy.evaluate(combined, risk)

                    if action.action.value == "hold":
                        logger.debug(f"[{token}] Strategy says HOLD: {action.reasoning[:80]}")
                        continue

                    # ─── Phase 8: Execute ───
                    self.state = AgentState.EXECUTING
                    execution_result = await self._execute_action(action, combined, risk)

                    # ─── Phase 9: Record & Learn ───
                    self.state = AgentState.POST_TRADE
                    self.memory.record_decision(combined, action)

                    if execution_result:
                        self._execution_history.append(execution_result)
                        trade_record = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "action": action.action.value,
                            "token": action.token,
                            "chain": action.chain.value,
                            "amount_in": execution_result.amount_in,
                            "amount_out": execution_result.amount_out,
                            "signal_confidence": combined.confidence,
                            "signal_type": combined.signal_type.value,
                            "reasoning": action.reasoning,
                            "mode": self.config.trading_mode,
                            "simulated": execution_result.simulated,
                            "tx_hash": execution_result.tx_hash,
                        }
                        self._trade_history.append(trade_record)

                        # Persist to database if available
                        if self.db:
                            try:
                                await self.db.save_trade(trade_record)
                            except Exception as db_err:
                                logger.warning(f"Failed to persist trade: {db_err}")

                except Exception as e:
                    logger.error(f"Error processing {token}/{chain.value}: {e}")

    def _summarize_technical_signals(self, signals: list) -> str:
        """Convert technical signals into a human-readable summary for LLM."""
        if not signals:
            return "No technical signals generated"
        parts = []
        for s in signals:
            direction = "↑" if s.signal_type.value == "bullish" else "↓" if s.signal_type.value == "bearish" else "→"
            parts.append(f"{direction} {s.indicator}: {s.description} (conf={s.confidence:.2f})")
        return "; ".join(parts)

    async def _execute_action(
        self,
        action: StrategyAction,
        signal: CombinedSignal,
        risk: RiskAssessment,
    ) -> Optional[ExecutionResult]:
        """Execute a trading action via the appropriate chain executor."""
        # Safety check
        if not self.risk_manager.is_trading_allowed:
            logger.warning("Trading not allowed - risk manager blocked")
            return None

        # Calculate position size in USD
        position_usd = min(
            action.position_size_pct * self._portfolio_usd
            if self._portfolio_usd > 0
            else risk.max_position_usd,
            risk.max_position_usd,
        )

        if position_usd <= 0:
            logger.warning("Position size is 0, skipping")
            return None

        # Determine swap direction
        if action.action.value == "buy":
            from_token, to_token = "USDC", action.token
            amount = position_usd
        elif action.action.value == "sell":
            from_token, to_token = action.token, "USDC"
            amount = position_usd
        else:
            return None

        # Get executor for the chain
        executor = self.executors.get(action.chain)
        if executor is None:
            logger.warning(
                f"No executor registered for {action.chain.value}. "
                f"Available: {[c.value for c in self.executors]}"
            )
            return None

        logger.info(
            f"Executing {action.action.value} {action.token} on {action.chain.value} "
            f"| Size: ${position_usd:.2f} | Confidence: {signal.confidence:.2f} "
            f"| Mode: {'PAPER' if executor.paper_mode else 'LIVE'}"
        )

        # Execute swap
        try:
            result = await executor.execute_swap(
                from_token=from_token,
                to_token=to_token,
                amount=amount,
                slippage_bps=100,
                wallet_address=self._wallet_address if not executor.paper_mode else None,
                private_key=self._private_key if not executor.paper_mode else None,
            )
            return result
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return None

    def stop(self) -> None:
        """Stop the agent."""
        self._running = False
        logger.info(f"QuantAgent-{self.agent_id} stopping...")

    def get_portfolio_summary(self) -> dict:
        """Get portfolio summary."""
        return {
            "portfolio_usd": self._portfolio_usd,
            "active_orders": len(self._active_orders),
            "total_trades": len(self._trade_history),
            "trading_mode": self.config.trading_mode,
            "risk_allowed": self.risk_manager.is_trading_allowed,
            "execution_count": len(self._execution_history),
        }

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Get recent trade history."""
        return self._trade_history[-limit:]
