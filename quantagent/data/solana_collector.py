"""Solana chain data provider (stub).

Used by ``backtest/runner.py`` and ``execution/solana_executor.py``:
  * ``SolanaProvider(rpc_url)``
  * ``await provider.get_price("SOL")`` -> float
  * ``await provider.get_ohlcv(token, interval, limit)`` -> list[OHLCV]
  * ``await provider.close()``
"""

from __future__ import annotations

from typing import Any

from quantagent.common.models import OHLCV


class SolanaProvider:
    """Minimal stub for Solana RPC / DEX data."""

    def __init__(self, rpc_url: str = "") -> None:
        self.rpc_url = rpc_url

    async def get_price(self, token: str) -> float:
        raise NotImplementedError(
            "SolanaProvider.get_price is not implemented. Query Jupiter/Birdeye."
        )

    async def get_ohlcv(
        self, token: str, interval: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        raise NotImplementedError(
            "SolanaProvider.get_ohlcv is not implemented."
        )

    async def close(self) -> None:
        return None
