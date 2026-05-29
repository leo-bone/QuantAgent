"""Backtesting engine for strategy validation.

Replays historical OHLCV data through the signal generation pipeline
and evaluates strategy performance with institutional-grade metrics.

Key features:
- Event-driven replay of historical candles
- Supports any signal generator + strategy combination
- Calculates Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor
- Handles position sizing and risk management
- Produces detailed trade log and equity curve
"""

from quantagent.backtest.engine import BacktestEngine
from quantagent.backtest.runner import BacktestRunner, run_backtest

__all__ = ["BacktestEngine", "BacktestRunner", "run_backtest"]
