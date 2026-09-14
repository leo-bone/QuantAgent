"""Market-data layer: abstraction + offline-safe default implementation.

This module is intentionally dependency-light so the package imports cleanly
even when no external data provider is installed. The agent uses it for
backtest / paper mode without ever touching the network.

Public surface:
  * ``MarketData``      — abstract interface with ``get_ohlcv``.
  * ``MockMarketData``  — deterministic synthetic OHLCV (no network, no deps).
                         This is the default and is safe to use offline.
  * ``CSVMarketData``   — optional, reads OHLCV from a local CSV (needs pandas).
  * ``CcxtMarketData``  — optional, pulls live OHLCV via ``ccxt``. The ``ccxt``
                         import is guarded so importing this module NEVER fails
                         when ccxt is missing; only constructing/instantiating
                         ``CcxtMarketData`` raises if ccxt is unavailable.
  * ``get_market_data`` — factory that selects a source ("mock" | "csv" | "ccxt").
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from quantagent.common.models import Chain, OHLCV


class MarketData(ABC):
    """Interface for market-data providers.

    Concrete implementations only need to return OHLCV candles (oldest first)
    for a given trading symbol / timeframe.
    """

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        chain: str = "solana",
    ) -> list[OHLCV]:
        """Return ``limit`` OHLCV candles for ``symbol`` (oldest first)."""
        ...


class MockMarketData(MarketData):
    """Deterministic synthetic OHLCV generator.

    No network and no third-party dependencies — this is the default source so
    backtest / paper mode runs anywhere. Output is stable per (symbol, seed).
    """

    def __init__(self, base_price: float = 100.0, seed: int = 0) -> None:
        self.base_price = base_price
        self.seed = seed

    @staticmethod
    def _seed_for(symbol: str, seed: int) -> int:
        # Stable int seed derived from the symbol (avoid str hash salt).
        return (sum(ord(c) for c in symbol) + seed) & 0xFFFFFFFF

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        chain: str = "solana",
    ) -> list[OHLCV]:
        try:
            chain_enum = Chain(chain)
        except ValueError:
            chain_enum = Chain.SOLANA

        minutes = {
            "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
        }.get(timeframe, 60)

        rng = random.Random(self._seed_for(symbol, self.seed))
        now = datetime.now(timezone.utc)
        candles: list[OHLCV] = []
        price = self.base_price

        for i in range(limit):
            drift = rng.uniform(-0.02, 0.02)  # +/-2% step
            open_ = price
            close = max(price * (1 + drift), 1e-9)
            high = max(open_, close) * (1 + abs(rng.uniform(0, 0.01)))
            low = min(open_, close) * (1 - abs(rng.uniform(0, 0.01)))
            volume = rng.uniform(0.5, 5.0) * self.base_price
            timestamp = now - timedelta(minutes=minutes * (limit - i))
            candles.append(
                OHLCV(
                    timestamp=timestamp,
                    open=round(open_, 8),
                    high=round(high, 8),
                    low=round(low, 8),
                    close=round(close, 8),
                    volume=round(volume, 4),
                    token=symbol,
                    chain=chain_enum,
                    interval=timeframe,
                )
            )
            price = close

        return candles


class CSVMarketData(MarketData):
    """Read OHLCV from a local CSV file. Requires ``pandas`` (optional dep)."""

    def __init__(self, path: str) -> None:
        self.path = path
        try:
            import pandas  # noqa: F401  (verify availability at construction)
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "CSVMarketData requires the optional 'pandas' package."
            ) from exc

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        chain: str = "solana",
    ) -> list[OHLCV]:
        import pandas as pd

        try:
            chain_enum = Chain(chain)
        except ValueError:
            chain_enum = Chain.SOLANA

        df = pd.read_csv(self.path).tail(limit)
        candles: list[OHLCV] = []
        for _, row in df.iterrows():
            ts = row.get("timestamp", datetime.now(timezone.utc))
            if not isinstance(ts, datetime):
                ts = pd.to_datetime(ts).to_pydatetime()
            candles.append(
                OHLCV(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                    token=symbol,
                    chain=chain_enum,
                    interval=timeframe,
                )
            )
        return candles


class CcxtMarketData(MarketData):
    """Live OHLCV via the optional ``ccxt`` package.

    Constructing this class imports ``ccxt`` and raises RuntimeError if it is
    not installed, but importing the module itself is always safe.
    """

    def __init__(self, exchange_id: str = "binance") -> None:
        try:
            import ccxt  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "CcxtMarketData requires the optional 'ccxt' package "
                "(pip install ccxt). Use MockMarketData for offline mode."
            ) from exc
        self.exchange_id = exchange_id
        self._ccxt = ccxt

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        chain: str = "solana",
    ) -> list[OHLCV]:
        try:
            chain_enum = Chain(chain)
        except ValueError:
            chain_enum = Chain.SOLANA

        exchange = getattr(self._ccxt, self.exchange_id)()
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        candles: list[OHLCV] = []
        for row in raw:
            ts, o, h, l, c, v = row
            candles.append(
                OHLCV(
                    timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(v),
                    token=symbol,
                    chain=chain_enum,
                    interval=timeframe,
                )
            )
        return candles


def get_market_data(source: str = "mock", **kwargs) -> MarketData:
    """Factory: pick a data source.

    ``source`` is one of "mock" (default, offline), "csv" (needs ``path``),
    or "ccxt" (needs optional ccxt; ``exchange_id`` optional).
    """
    if source == "csv":
        return CSVMarketData(kwargs["path"])
    if source == "ccxt":
        return CcxtMarketData(kwargs.get("exchange_id", "binance"))
    return MockMarketData(**kwargs)
