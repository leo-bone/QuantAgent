"""BSC chain data provider (stub).

Used by ``backtest/runner.py``:
  * ``BSCProvider(rpc_url)``
  * ``await provider.get_ohlcv(token, interval, limit)`` -> list[OHLCV]
  * ``await provider.close()``
"""

from __future__ import annotations

from typing import Any

from quantagent.common.models import OHLCV


class BSCProvider:
    """Minimal stub for BSC RPC / DEX data."""

    def __init__(self, rpc_url: str = "") -> None:
        self.rpc_url = rpc_url

    async def get_ohlcv(
        self, token: str, interval: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        raise NotImplementedError(
            "BSCProvider.get_ohlcv is not implemented. Query PancakeSwap/Birdeye."
        )

    async def close(self) -> None:
        return None
