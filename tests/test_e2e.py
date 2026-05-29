"""End-to-end integration test for QuantAgent.

Runs the full pipeline:
    Data Collection → Signal Generation → Signal Fusion → Strategy → Risk → Execution → Memory

Two modes:
    --mock    Use synthetic OHLCV data (offline, no API key needed)
    --live    Use real CoinGecko data (requires internet)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from loguru import logger

from quantagent.common.models import (
    AgentConfig,
    Chain,
    SignalType,
)
from quantagent.data.ohlcv_provider import fetch_ohlcv, generate_mock_ohlcv
from quantagent.signal.technical import TechnicalSignalGenerator
from quantagent.signal.combiner import SignalCombiner
from quantagent.signal.llm_analyzer import LLMAnalyzer
from quantagent.strategy.momentum import MomentumStrategy
from quantagent.strategy.risk_manager import RiskManager
from quantagent.agent.memory import AgentMemoryStore
from quantagent.execution.solana_executor import SolanaExecutor
from quantagent.execution.bsc_executor import BSCExecutor


# ──────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────

def ohlcv_to_df(candles) -> pd.DataFrame:
    """Convert list of OHLCV models to a pandas DataFrame."""
    return pd.DataFrame([{
        "timestamp": c.timestamp,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    } for c in candles])


async def run_e2e(use_mock: bool = True, tokens: list[str] | None = None):
    """Run the complete end-to-end pipeline."""

    tokens = tokens or ["SOL", "BNB"]
    chain_map = {"SOL": Chain.SOLANA, "BNB": Chain.BSC}

    logger.info("=" * 60)
    logger.info("QuantAgent E2E Integration Test")
    logger.info(f"Mode: {'MOCK' if use_mock else 'LIVE (CoinGecko)'}")
    logger.info(f"Tokens: {tokens}")
    logger.info("=" * 60)

    # ─── 1. Data Collection ────────────────────────────────
    logger.info("\n📊 Phase 1: Data Collection")
    all_data = {}

    for token in tokens:
        chain = chain_map.get(token, Chain.SOLANA)
        chain_str = "solana" if chain == Chain.SOLANA else "bsc"

        if use_mock:
            base_prices = {"SOL": 172.50, "BNB": 680.0, "CAKE": 2.15, "JUP": 0.95}
            candles = generate_mock_ohlcv(
                token=token,
                chain=chain_str,
                base_price=base_prices.get(token, 100.0),
                num_candles=100,
                volatility=0.02,
            )
        else:
            candles = await fetch_ohlcv(token, chain=chain_str, interval="1h", limit=100)

        if not candles:
            logger.error(f"❌ No OHLCV data for {token}")
            continue

        df = ohlcv_to_df(candles)
        all_data[token] = (candles, df, chain)
        logger.info(
            f"  ✅ {token}: {len(candles)} candles | "
            f"Price: ${candles[0].close:.2f} → ${candles[-1].close:.2f} | "
            f"Vol: ${sum(c.volume for c in candles):,.0f}"
        )

    # ─── 2. Signal Generation ─────────────────────────────
    logger.info("\n📡 Phase 2: Signal Generation")
    tech_gen = TechnicalSignalGenerator()
    all_signals = {}

    for token, (candles, df, chain) in all_data.items():
        signals = await tech_gen.generate_signals(token, chain, df)
        all_signals[token] = signals
        if signals:
            for s in signals:
                logger.info(
                    f"  ✅ {token} [{s.indicator}]: {s.signal_type.value} | "
                    f"Conf: {s.confidence:.2f} | {s.description}"
                )
        else:
            logger.info(f"  ⚪ {token}: No technical signals triggered")

    # ─── 3. Signal Fusion ─────────────────────────────────
    logger.info("\n🔀 Phase 3: Signal Fusion")
    combiner = SignalCombiner()
    combined_signals = {}

    for token, signals in all_signals.items():
        if token not in all_data:
            continue
        _, _, chain = all_data[token]

        combined = combiner.combine(
            token=token,
            chain=chain,
            technical_signals=signals,
            onchain_signals=[],  # No on-chain data in this test
            llm_signal=None,     # No LLM in this test
        )
        combined_signals[token] = combined
        n_sources = (
            len(combined.technical_signals) +
            len(combined.onchain_signals) +
            (1 if combined.llm_signal else 0)
        )
        logger.info(
            f"  ✅ {token}: {combined.signal_type.value} | "
            f"Conf: {combined.confidence:.2f} | "
            f"Sources: {n_sources}"
        )

    # ─── 4. Strategy Evaluation ───────────────────────────
    logger.info("\n🎯 Phase 4: Strategy Evaluation")
    strategy = MomentumStrategy()

    for token, combined in combined_signals.items():
        if token not in all_data:
            continue
        candles, df, chain = all_data[token]

        # Risk assessment
        risk_mgr = RiskManager(
            max_position_size_usd=1000.0,
            max_daily_loss_usd=200.0,
        )
        risk = risk_mgr.assess_risk(combined, portfolio_usd=10000.0)

        # Strategy
        action = strategy.evaluate(combined, risk)

        sl_str = f"${action.stop_loss:.2f}" if action.stop_loss else "N/A"
        tp_str = f"${action.take_profit:.2f}" if action.take_profit else "N/A"
        logger.info(
            f"  ✅ {token}: {action.action.value.upper()} | "
            f"Size: {action.position_size_pct:.1%} | "
            f"SL: {sl_str} | TP: {tp_str} | "
            f"Risk Score: {risk.risk_score:.2f} | "
            f"Reason: {action.reasoning[:60]}..."
        )

    # ─── 5. Execution (Paper Mode) ───────────────────────
    logger.info("\n⚡ Phase 5: Paper Execution")
    sol_executor = SolanaExecutor(
        rpc_url="https://api.mainnet-beta.solana.com",
        paper_mode=True,
    )
    bsc_executor = BSCExecutor(
        rpc_url="https://bsc-dataseed1.binance.org/",
        paper_mode=True,
    )

    for token, combined in combined_signals.items():
        if combined.signal_type == SignalType.NEUTRAL:
            logger.info(f"  ⚪ {token}: Neutral signal, skipping execution")
            continue
        if token not in all_data:
            continue
        _, _, chain = all_data[token]

        candles_list = all_data[token][0]
        current_price = candles_list[-1].close

        if chain == Chain.SOLANA:
            result = await sol_executor.execute_swap(
                from_token="USDC",
                to_token=token,
                amount=100.0,
                slippage_bps=100,
            )
        else:
            result = await bsc_executor.execute_swap(
                from_token="USDT",
                to_token=token,
                amount=100.0,
                slippage_bps=100,
            )

        logger.info(
            f"  ✅ {token}: {result.status.value} | "
            f"Amount In: {result.amount_in:.2f} | "
            f"Amount Out: {result.amount_out:.4f} | "
            f"Price: {result.effective_price:.4f} | "
            f"TX: {result.tx_hash or 'N/A'}"
        )

    # ─── 6. Memory & Performance ──────────────────────────
    logger.info("\n🧠 Phase 6: Memory Store")
    memory = AgentMemoryStore()

    for token, combined in combined_signals.items():
        if token not in all_data:
            continue
        _, _, chain = all_data[token]
        risk_mgr = RiskManager(max_position_size_usd=1000.0, max_daily_loss_usd=200.0)
        risk = risk_mgr.assess_risk(combined, portfolio_usd=10000.0)
        action = strategy.evaluate(combined, risk)
        memory.record_decision(combined, action)

    stats = memory.get_performance_stats()
    logger.info(f"  ✅ Decisions recorded: {stats['total']}")
    logger.info(f"  ✅ Win rate: {stats['win_rate']:.1%}")

    # ─── Summary ──────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("🎉 E2E Integration Test PASSED!")
    logger.info(f"   Tokens tested: {list(all_data.keys())}")
    logger.info(f"   Signals generated: {sum(len(s) for s in all_signals.values())}")
    logger.info(f"   Combined signals: {len(combined_signals)}")
    logger.info(f"   Decisions recorded: {stats['total']}")
    logger.info("=" * 60)

    return True


# ──────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantAgent E2E Test")
    parser.add_argument("--live", action="store_true", help="Use real CoinGecko data")
    parser.add_argument("--tokens", nargs="+", default=["SOL", "BNB"], help="Tokens to test")
    args = parser.parse_args()

    success = asyncio.run(run_e2e(use_mock=not args.live, tokens=args.tokens))
    sys.exit(0 if success else 1)
