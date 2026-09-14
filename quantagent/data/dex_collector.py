"""DEX / market data collector (stub).

Provides the ``DexCollector`` class and ``create_dex_collector`` factory that the
agent, API and dashboard import. The real implementation should pull live quotes
from Jupiter (Solana) / PancakeSwap (BSC), the Fear & Greed index from a market
sentiment API, and run per-chain health checks.

Methods called by the codebase:
  * ``get_price(token, chain)`` -> float
  * ``get_fear_greed_index()`` -> int (0-100)
  * ``get_all_prices(token_list)`` -> dict[str, float]
  * ``health_check_all()`` -> dict[str, bool]
"""

from __future__ import annotations

from typing import Any


class DexCollector:
    """Minimal stub for the DEX data collector."""

    def __init__(
        self,
        solana_rpc: str = "",
        jupiter_url: str = "",
        bsc_rpc: str = "",
        pancake_router: str = "",
    ) -> None:
        self.solana_rpc = solana_rpc
        self.jupiter_url = jupiter_url
        self.bsc_rpc = bsc_rpc
        self.pancake_router = pancake_router

    async def get_price(self, token: str, chain: str) -> float:
        """Return the current price (USD) for ``token`` on ``chain``."""
        raise NotImplementedError(
            "DexCollector.get_price is not implemented. Provide a live Jupiter/"
            "PancakeSwap quote client."
        )

    async def get_fear_greed_index(self) -> int:
        """Return the market Fear & Greed index (0-100)."""
        raise NotImplementedError(
            "DexCollector.get_fear_greed_index is not implemented."
        )

    async def get_all_prices(self, token_list: list[str]) -> dict[str, float]:
        """Return a {token: price} map for all requested tokens."""
        raise NotImplementedError(
            "DexCollector.get_all_prices is not implemented."
        )

    async def health_check_all(self) -> dict[str, bool]:
        """Return a {chain: healthy} map for all configured chains."""
        raise NotImplementedError(
            "DexCollector.health_check_all is not implemented."
        )


def create_dex_collector(
    solana_rpc: str = "",
    jupiter_url: str = "",
    bsc_rpc: str = "",
    pancake_router: str = "",
) -> DexCollector:
    """Factory used by ``start_agent.py`` and ``api/app.py``."""
    return DexCollector(
        solana_rpc=solana_rpc,
        jupiter_url=jupiter_url,
        bsc_rpc=bsc_rpc,
        pancake_router=pancake_router,
    )
