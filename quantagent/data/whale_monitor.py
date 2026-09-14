"""Whale / large-transfer monitor (stub).

Constructed as ``WhaleMonitor(dex_collector)`` by ``quant_agent.py`` and used via
``check_large_transfers(token, chain)``. The real implementation should watch
chain mempools / account activity for unusually large transfers.
"""

from __future__ import annotations

from typing import Any


class WhaleMonitor:
    """Minimal stub for on-chain whale monitoring."""

    def __init__(self, dex_collector: Any = None) -> None:
        self.dex_collector = dex_collector

    async def check_large_transfers(
        self, token: str, chain: Any
    ) -> list[dict[str, Any]]:
        """Return a list of large-transfer signal dicts for ``token`` on ``chain``."""
        raise NotImplementedError(
            "WhaleMonitor.check_large_transfers is not implemented. Subscribe to "
            "chain RPC websockets and surface transfers above a threshold."
        )
