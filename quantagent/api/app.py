"""QuantAgent FastAPI application."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from loguru import logger

from quantagent.common.models import AgentConfig, AgentState, Chain
from quantagent.data.dex_collector import create_dex_collector
from quantagent.agent.quant_agent import QuantAgent


# Global agent instance
_agent: Optional[QuantAgent] = None
_agent_task: Optional[asyncio.Task] = None


def get_agent() -> QuantAgent:
    """Get or create the global agent instance."""
    global _agent
    if _agent is None:
        raise RuntimeError("Agent not initialized. Call init_agent() first.")
    return _agent


async def init_agent() -> QuantAgent:
    """Initialize the agent with default configuration."""
    global _agent

    if _agent is not None:
        return _agent

    from config.settings import settings

    config = AgentConfig(
        name="QuantAgent-01",
        chains=[Chain.SOLANA, Chain.BSC],
        tokens_to_monitor=["SOL", "BNB", "BTC", "ETH", "USDC"],
        strategy="momentum",
        trading_mode=settings.trading_mode,
        max_position_size_usd=settings.max_position_size_usd,
        max_daily_loss_usd=settings.max_daily_loss_usd,
    )

    collector = create_dex_collector(
        solana_rpc=settings.solana_rpc_url,
        jupiter_url=settings.jupiter_api_url,
        bsc_rpc=settings.bsc_rpc_url,
        pancake_router=settings.pancakeswap_router,
    )

    agent = QuantAgent(
        config=config,
        dex_collector=collector,
        llm_api_key=settings.openai_api_key,
    )

    _agent = agent
    logger.info(f"Agent initialized: {config.name} ({config.trading_mode} mode)")
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    agent = await init_agent()
    logger.info("QuantAgent API server started")
    yield
    # Shutdown
    if _agent_task and not _agent_task.done():
        _agent_task.cancel()
    agent.stop()
    logger.info("QuantAgent API server shutdown")


app = FastAPI(
    title="QuantAgent API",
    description="AI-powered on-chain quantitative trading agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routes ───

@app.get("/")
async def root():
    return {"name": "QuantAgent", "version": "0.1.0", "status": "running"}


@app.get("/health")
async def health():
    """Health check endpoint with circuit breaker and service status."""
    from quantagent.infrastructure import get_all_breaker_status, get_metrics

    agent = get_agent()
    chain_health = await agent.dex_collector.health_check_all()

    return {
        "agent_state": agent.state.value,
        "chains": {c.value: ok for c, ok in chain_health.items()},
        "trading_allowed": agent.risk_manager.is_trading_allowed,
        "circuit_breakers": get_all_breaker_status(),
        "metrics_summary": {
            "total_cycles": get_metrics()._total_cycles,
            "total_signals": get_metrics()._total_signals,
            "total_trades": get_metrics()._total_trades,
            "total_errors": get_metrics()._total_errors,
        },
    }


@app.get("/agent/status")
async def agent_status():
    """Get current agent status."""
    agent = get_agent()
    return agent.status.model_dump()


@app.post("/agent/start")
async def start_agent(poll_interval: int = 60):
    """Start the agent monitoring loop."""
    global _agent_task
    agent = get_agent()

    if _agent_task and not _agent_task.done():
        return {"status": "already_running"}

    _agent_task = asyncio.create_task(agent.run(poll_interval=poll_interval))
    return {"status": "started", "poll_interval": poll_interval}


@app.post("/agent/stop")
async def stop_agent():
    """Stop the agent."""
    agent = get_agent()
    agent.stop()
    return {"status": "stopping"}


@app.post("/agent/emergency-stop")
async def emergency_stop():
    """Trigger emergency stop - halts all trading immediately."""
    agent = get_agent()
    agent.risk_manager.set_emergency_stop(True)
    agent.stop()
    return {"status": "emergency_stop_activated"}


@app.get("/agent/portfolio")
async def portfolio():
    """Get portfolio summary."""
    agent = get_agent()
    return agent.get_portfolio_summary()


@app.get("/agent/memory")
async def memory(limit: int = 50):
    """Get recent agent decisions."""
    agent = get_agent()
    decisions = agent.memory.get_recent_decisions(limit=limit)
    return {
        "count": len(decisions),
        "decisions": [d.model_dump() for d in decisions],
    }


@app.get("/agent/performance")
async def performance():
    """Get performance statistics."""
    agent = get_agent()
    return agent.memory.get_performance_stats()


@app.get("/prices")
async def get_prices(tokens: str = "SOL,BNB,ETH,BTC"):
    """Get current prices for tokens across chains."""
    agent = get_agent()
    token_list = [t.strip() for t in tokens.split(",")]
    prices = await agent.dex_collector.get_all_prices(token_list)
    return prices


@app.get("/fear-greed")
async def fear_greed():
    """Get Fear & Greed Index."""
    agent = get_agent()
    return await agent.dex_collector.get_fear_greed_index()


@app.get("/metrics")
async def metrics():
    """Get comprehensive runtime metrics."""
    from quantagent.infrastructure import get_metrics, get_all_breaker_status
    return {
        "metrics": get_metrics().get_summary(),
        "circuit_breakers": get_all_breaker_status(),
    }


@app.get("/backtest/result")
async def backtest_info():
    """Get backtest framework info."""
    return {
        "available": True,
        "description": "Run backtests via POST /backtest/run",
        "supported_chains": ["solana", "bsc"],
        "supported_tokens": ["SOL", "BNB", "ETH", "BTC", "USDC"],
        "metrics": ["sharpe_ratio", "sortino_ratio", "max_drawdown", "win_rate", "profit_factor", "calmar_ratio"],
    }


@app.post("/backtest/run")
async def run_backtest(
    token: str = "SOL",
    chain: str = "solana",
    days: int = 30,
    initial_capital: float = 10000.0,
):
    """Run a backtest for a given token."""
    from quantagent.backtest import run_backtest as _run_backtest
    chain_enum = Chain.SOLANA if chain == "solana" else Chain.BSC
    try:
        result = await _run_backtest(
            token=token,
            chain=chain_enum,
            days=days,
            initial_capital=initial_capital,
        )
        return result.to_summary()
    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ─── Evolution Endpoints ───

@app.get("/evolution/status")
async def evolution_status():
    """Get current evolution engine status."""
    from quantagent.evolution.engine import EvolutionEngine
    engine = _get_evolution_engine()
    status = engine.get_status()

    # Add reflection status
    from quantagent.evolution.reflection import SelfReflection
    reflection = _get_reflection()
    status["reflection"] = reflection.get_status()

    return status


@app.post("/evolution/trigger")
async def trigger_evolution(
    population_size: int = 20,
    max_generations: int = 10,
    token: str = "SOL",
    chain: str = "solana",
    days: int = 30,
    auto_deploy: bool = True,
):
    """Trigger an evolution run.

    The evolution engine will:
        1. Create/extend a population of strategy genomes
        2. Evaluate each via backtesting
        3. Select, crossover, and mutate
        4. Auto-deploy the best genome if fitness > threshold
    """
    from quantagent.evolution.engine import EvolutionEngine, EvolutionConfig

    config = EvolutionConfig(
        population_size=population_size,
        max_generations=max_generations,
        backtest_token=token,
        backtest_chain=chain,
        backtest_days=days,
        auto_deploy=auto_deploy,
    )

    engine = EvolutionEngine(config=config)
    result = await engine.evolve()
    return result.to_summary()


@app.get("/evolution/active-genome")
async def get_active_genome():
    """Get the currently deployed active genome."""
    from quantagent.evolution.engine import EvolutionEngine
    genome = EvolutionEngine.load_active_genome()
    if genome:
        return genome.summary()
    return {"active_genome": None, "message": "No genome deployed yet"}


@app.post("/evolution/reflect")
async def trigger_reflection():
    """Trigger a self-reflection cycle on recent trades."""
    from quantagent.evolution.reflection import SelfReflection
    reflection = _get_reflection()

    result = await reflection.reflect()
    return result.to_dict()


@app.get("/evolution/history")
async def evolution_history(limit: int = 20):
    """Get recent evolution history."""
    from quantagent.evolution.engine import EvolutionEngine
    engine = _get_evolution_engine()
    return {
        "generation": engine.generation,
        "best_fitness_ever": engine.best_fitness_ever,
        "recent_history": engine.history[-limit:],
    }


# ─── Evolution singletons ───

_evolution_engine = None
_reflection = None


def _get_evolution_engine():
    global _evolution_engine
    if _evolution_engine is None:
        from quantagent.evolution.engine import EvolutionEngine
        _evolution_engine = EvolutionEngine()
    return _evolution_engine


def _get_reflection():
    global _reflection
    if _reflection is None:
        from quantagent.evolution.reflection import SelfReflection
        _reflection = SelfReflection()
    return _reflection


# ─── WebSocket ───

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time agent updates."""
    await websocket.accept()
    agent = get_agent()

    try:
        while True:
            # Send agent status every 5 seconds
            status = agent.status.model_dump()
            await websocket.send_json(status)
            await asyncio.sleep(5)
    except Exception:
        pass
