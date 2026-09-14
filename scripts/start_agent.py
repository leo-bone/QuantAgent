#!/usr/bin/env python3
"""Start the QuantAgent trading agent.

Usage:
    python scripts/start_agent.py --mode paper
    python scripts/start_agent.py --mode paper --poll 30
    python scripts/start_agent.py --mode live  # ⚠️ Real money!
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Clear proxy settings that block RPC/API calls ───
# Clash or other local proxies (127.0.0.1:59943 etc.) intercept HTTPS requests
# to Solana/BSC RPCs and CoinGecko, causing timeouts.  Unset them unless
# the user explicitly wants to keep them (set QUANTAGENT_KEEP_PROXY=1).
if not os.environ.get("QUANTAGENT_KEEP_PROXY"):
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)
    # Set no_proxy to allow local connections without proxy
    os.environ["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"

from loguru import logger

# Configure logging
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)
logger.add(PROJECT_ROOT / "quantagent.log", rotation="10 MB", level="DEBUG")


def parse_args():
    """Parse command-line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Start QuantAgent")
    parser.add_argument(
        "--mode", default="paper", choices=["paper", "live"],
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--poll", default=60, type=int,
        help="Polling interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--tokens", default="SOL,BNB,ETH,BTC,USDC",
        help="Comma-separated tokens to monitor",
    )
    parser.add_argument(
        "--chains", default="solana,bsc",
        help="Comma-separated chains to monitor",
    )
    return parser.parse_args()


async def run_agent(mode: str, poll: int, tokens: str, chains: str):
    """Build and run the agent."""
    from config.settings import settings
    from quantagent.common.models import AgentConfig, Chain
    from quantagent.data.dex_collector import create_dex_collector
    from quantagent.agent.quant_agent import QuantAgent
    from quantagent.execution.solana_executor import SolanaExecutor
    from quantagent.execution.bsc_executor import BSCExecutor
    from quantagent.data.database import Database

    # Override settings
    settings.trading_mode = mode

    # Load .env if exists
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        logger.info(f"Loaded config from {env_file}")

    # Parse chains
    chain_list = []
    for c in chains.split(","):
        try:
            chain_list.append(Chain(c.strip()))
        except ValueError:
            logger.warning(f"Unknown chain: {c}")

    # Parse tokens
    token_list = [t.strip() for t in tokens.split(",")]

    # Build config
    config = AgentConfig(
        name="QuantAgent-01",
        chains=chain_list,
        tokens_to_monitor=token_list,
        trading_mode=mode,
        max_position_size_usd=settings.max_position_size_usd,
        max_daily_loss_usd=settings.max_daily_loss_usd,
    )

    # Create data collector
    collector = create_dex_collector(
        solana_rpc=settings.solana_rpc_url,
        jupiter_url=settings.jupiter_api_url,
        bsc_rpc=settings.bsc_rpc_url,
        pancake_router=settings.pancakeswap_router,
    )

    is_paper = mode == "paper"

    # Create executors
    solana_executor = SolanaExecutor(
        rpc_url=settings.solana_rpc_url,
        paper_mode=is_paper,
    )
    bsc_executor = BSCExecutor(
        rpc_url=settings.bsc_rpc_url,
        paper_mode=is_paper,
        router_address=settings.pancakeswap_router,
    )

    # Build agent with all components
    # Database: use SQLite by default, PostgreSQL only if explicitly configured
    db_url = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'quantagent.db'}"
    if settings.database_url and "postgresql" in settings.database_url:
        db_url = settings.database_url

    # ─── SAFETY GUARD: wallet_address must be the PUBLIC address, NEVER the private key ───
    # Passing the private key as the wallet address would leak it to the executor /
    # Jupiter. This guard prevents that class of leak from ever recurring.

    # SECURITY: private keys are ONLY ever read from the environment and used
    # locally for signing. They are never stored in settings/config, never
    # printed, and never sent to any API / the blockchain. If unset, live mode
    # below refuses to start.
    wallet_address = settings.solana_wallet_address if not is_paper else ""
    private_key = os.environ.get("SOLANA_PRIVATE_KEY", "") if not is_paper else ""
    bsc_private_key = os.environ.get("BSC_PRIVATE_KEY", "") if not is_paper else ""
    if not is_paper:
        if not wallet_address:
            raise RuntimeError(
                "SECURITY: solana_wallet_address is empty in live mode — "
                "refusing to construct executor without a public wallet address."
            )
        if wallet_address == private_key:
            raise RuntimeError(
                "SECURITY: wallet_address must be the public address, not the private key. "
                "Refusing to start to prevent leaking the private key."
            )
        # Heuristic: a Solana private key is a long secret (>=64 chars), while a
        # public address is short base58 (~44 chars). Reject anything that looks like a key.
        if len(wallet_address) >= 64:
            raise RuntimeError(
                "SECURITY: wallet_address appears to be a private key, not a public address. "
                "Refusing to start."
            )

    agent = QuantAgent(
        config=config,
        dex_collector=collector,
        llm_api_key=settings.openai_api_key,
        birdeye_api_key=settings.birdeye_api_key,
        wallet_address=wallet_address,
        private_key=private_key,
        bsc_private_key=bsc_private_key,
        database=Database(db_url),
    )

    # Register executors
    agent.register_executor(Chain.SOLANA, solana_executor)
    agent.register_executor(Chain.BSC, bsc_executor)

    # Safety warning for live mode
    if mode == "live":
        logger.warning("⚠️  LIVE TRADING MODE - Real money at risk!")
        logger.warning("⚠️  Make sure you have set proper risk limits")
        logger.warning("⚠️  Press Ctrl+C to stop at any time")

    logger.info(f"Starting {config.name}")
    logger.info(f"Mode: {mode} | Tokens: {token_list}")
    logger.info(f"Chains: {[c.value for c in chain_list]} | Poll: {poll}s")
    logger.info(f"Max position: ${config.max_position_size_usd} | Max daily loss: ${config.max_daily_loss_usd}")
    logger.info(f"Executors: Solana(paper={is_paper}), BSC(paper={is_paper})")
    logger.info("=" * 50)

    # Graceful shutdown on Ctrl+C
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Received shutdown signal...")
        agent.stop()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # Run agent
    try:
        await agent.run(poll_interval=poll)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        agent.stop()
    except Exception as e:
        logger.error(f"Agent crashed: {e}")
        raise


def main():
    """Entry point."""
    args = parse_args()
    try:
        asyncio.run(run_agent(
            mode=args.mode,
            poll=args.poll,
            tokens=args.tokens,
            chains=args.chains,
        ))
    except KeyboardInterrupt:
        logger.info("Goodbye!")


if __name__ == "__main__":
    main()
