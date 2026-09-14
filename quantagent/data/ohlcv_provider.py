"""OHLCV data provider (stub with a working mock generator).

``fetch_ohlcv`` is the live path (Birdeye etc.) and raises
``NotImplementedError``. ``generate_mock_ohlcv`` is fully implemented because
it is used as a deterministic fallback by the dashboard, e2e tests and the
agent when no real data is available — keeping it functional avoids breaking
those code paths.

Call signatures used in the codebase:
  * ``fetch_ohlcv(token, chain, interval, limit, birdeye_api_key)`` -> list[OHLCV]
  * ``generate_mock_ohlcv(token, chain, base_price, num_candles)`` -> list[OHLCV]
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from quantagent.common.models import Chain, OHLCV


async def fetch_ohlcv(
    token: str,
    chain: str = "solana",
    interval: str = "1h",
    limit: int = 100,
    birdeye_api_key: str = "",
) -> list[OHLCV]:
    """Fetch historical OHLCV candles from a live provider (e.g. Birdeye).

    The real implementation should call the Birdeye (or equivalent) API using
    ``birdeye_api_key`` and map the response to ``OHLCV`` models.
    """
    raise NotImplementedError(
        "fetch_ohlcv is not implemented. Provide a Birdeye/OHLCV client and "
        "map the response to quantagent.common.models.OHLCV."
    )


def generate_mock_ohlcv(
    token: str,
    chain: str = "solana",
    base_price: float = 100.0,
    num_candles: int = 100,
    interval: str = "1h",
) -> list[OHLCV]:
    """Deterministic-ish mock candle generator used as a safe fallback."""
    try:
        chain_enum = Chain(chain)
    except ValueError:
        chain_enum = Chain.SOLANA

    candles: list[OHLCV] = []
    price = base_price
    now = datetime.now(timezone.utc)
    # Best-effort interval -> minutes mapping for timestamp spacing.
    minutes = {
        "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
    }.get(interval, 60)

    for i in range(num_candles):
        drift = random.uniform(-0.02, 0.02)  # +/-2% step
        open_ = price
        close = max(price * (1 + drift), 1e-9)
        high = max(open_, close) * (1 + abs(random.uniform(0, 0.01)))
        low = min(open_, close) * (1 - abs(random.uniform(0, 0.01)))
        volume = random.uniform(0.5, 5.0) * base_price
        timestamp = now - timedelta(minutes=minutes * (num_candles - i))
        candles.append(
            OHLCV(
                timestamp=timestamp,
                open=round(open_, 8),
                high=round(high, 8),
                low=round(low, 8),
                close=round(close, 8),
                volume=round(volume, 4),
                token=token,
                chain=chain_enum,
                interval=interval,
            )
        )
        price = close
    return candles
