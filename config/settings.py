"""QuantAgent global configuration"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # General
    app_name: str = "QuantAgent"
    app_env: str = "development"
    log_level: str = "INFO"
    trading_mode: str = "paper"  # paper | live

    # Solana
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_wss_url: str = "wss://api.mainnet-beta.solana.com"
    solana_private_key_encrypted: str = ""
    jupiter_api_url: str = "https://quote-api.jup.ag/v6"
    birdeye_api_key: str = ""  # Birdeye OHLCV data

    # BSC
    bsc_rpc_url: str = "https://bsc-dataseed.binance.org"
    bsc_wss_url: str = "wss://bsc-ws-node.nariox.org"
    bsc_private_key_encrypted: str = ""
    pancakeswap_router: str = "0x10ED43C718714eb63d5aA57B78B54704E256924E"

    # AI / LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_max_tokens: int = 2000

    # Database (default: SQLite for dev, PostgreSQL for production)
    database_url: str = ""  # Empty = auto SQLite; set postgresql+asyncpg://... for production
    redis_url: str = "redis://localhost:6379/0"

    # Security
    encryption_key: str = ""

    # Risk Management
    max_position_size_usd: float = 1000.0
    max_daily_loss_usd: float = 500.0
    max_slippage_bps: int = 100

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton
settings = Settings()
