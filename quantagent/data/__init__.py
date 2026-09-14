"""QuantAgent data layer (DEX/market data, on-chain signals, persistence).

This package was originally missing, causing an import-time crash. The modules
below are minimal, import-safe stubs. They provide the exact symbols the rest of
the codebase imports (classes/functions + signatures) so the agent starts
cleanly, but most methods raise ``NotImplementedError`` or return typed empty
structures, with guidance on what the real implementation must do.

Real implementations needed:
  * dex_collector  — live price quotes, fear&greed index, health checks via
                     Jupiter / Birdeye / RPC.
  * ohlcv_provider — historical OHLCV via Birdeye (live) or mock generator.
  * whale_monitor  — large-transfer / whale detection via RPC websockets.
  * database       — SQLAlchemy async engine + repositories.
  * solana_collector / bsc_collector — chain-specific OHLCV & price providers.
"""

from quantagent.data.dex_collector import DexCollector, create_dex_collector
from quantagent.data.ohlcv_provider import fetch_ohlcv, generate_mock_ohlcv
from quantagent.data.whale_monitor import WhaleMonitor
from quantagent.data.database import Database
from quantagent.data.market_data import (
    MarketData,
    MockMarketData,
    CSVMarketData,
    CcxtMarketData,
    get_market_data,
)

__all__ = [
    "DexCollector",
    "create_dex_collector",
    "fetch_ohlcv",
    "generate_mock_ohlcv",
    "WhaleMonitor",
    "Database",
    "MarketData",
    "MockMarketData",
    "CSVMarketData",
    "CcxtMarketData",
    "get_market_data",
]
