"""Backtest engine — core replay and performance calculation logic.

The engine replays historical OHLCV data candle-by-candle through
the signal generation and strategy evaluation pipeline, tracking
portfolio value and trade outcomes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from quantagent.common.models import (
    Chain,
    CombinedSignal,
    OHLCV,
    RiskAssessment,
    Signal,
    SignalSource,
    SignalType,
)


@dataclass
class Trade:
    """A single completed trade in the backtest."""
    entry_time: datetime
    exit_time: datetime
    token: str
    direction: str  # "LONG"
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    signal_confidence: float
    exit_reason: str  # "take_profit", "stop_loss", "end_of_data"

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestResult:
    """Complete results of a backtest run."""
    # Identity
    token: str
    chain: Chain
    start_date: datetime
    end_date: datetime
    initial_capital: float

    # Performance metrics
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_holding_period_bars: float = 0.0

    # Portfolio tracking
    equity_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    # Risk metrics
    avg_confidence: float = 0.0
    max_consecutive_losses: int = 0
    calmar_ratio: float = 0.0

    def to_summary(self) -> dict:
        """Return a clean summary dict for API/display."""
        return {
            "token": self.token,
            "chain": self.chain.value,
            "period": f"{self.start_date.date()} → {self.end_date.date()}",
            "initial_capital": self.initial_capital,
            "final_value": self.equity_curve[-1] if self.equity_curve else self.initial_capital,
            "total_return_pct": round(self.total_return_pct, 2),
            "annualized_return_pct": round(self.annualized_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 3),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_trade_pnl_pct": round(self.avg_trade_pnl_pct, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
        }


class BacktestEngine:
    """Event-driven backtest engine.

    Replays OHLCV data through signal generation and strategy evaluation,
    tracking portfolio state and computing performance metrics.

    Usage:
        engine = BacktestEngine(initial_capital=10000)
        result = engine.run(
            ohlcv_data=historical_candles,
            signal_generator=my_signal_gen,
            strategy=my_strategy,
            token="SOL",
            chain=Chain.SOLANA,
        )
        print(result.to_summary())
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        position_size_pct: float = 0.10,
        stop_loss_pct: float = 0.05,
        take_profit_ratio: float = 2.0,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.001,
    ):
        """
        Args:
            initial_capital: Starting portfolio value in USD
            position_size_pct: Fraction of portfolio per trade (0.10 = 10%)
            stop_loss_pct: Default stop loss percentage
            take_profit_ratio: Reward/risk ratio for take profit (2:1 default)
            commission_pct: Trading commission as fraction
            slippage_pct: Simulated slippage as fraction
        """
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_ratio = take_profit_ratio
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

    def run(
        self,
        ohlcv_data: list[OHLCV],
        signal_generator,
        strategy,
        token: str = "SOL",
        chain: Chain = Chain.SOLANA,
    ) -> BacktestResult:
        """Run backtest over historical data.

        Args:
            ohlcv_data: Historical OHLCV candles (chronologically ordered)
            signal_generator: Object with generate_signals(token, chain) method
            strategy: Object with evaluate(signal, risk, portfolio) method
            token: Token symbol being tested
            chain: Chain being tested

        Returns:
            BacktestResult with full performance metrics
        """
        if not ohlcv_data:
            logger.warning("No OHLCV data provided for backtest")
            return BacktestResult(
                token=token,
                chain=chain,
                start_date=datetime.now(timezone.utc),
                end_date=datetime.now(timezone.utc),
                initial_capital=self.initial_capital,
            )

        # Sort data chronologically
        ohlcv_data = sorted(ohlcv_data, key=lambda x: x.timestamp)

        # Initialize tracking state
        cash = self.initial_capital
        position: Optional[dict] = None  # {"entry_price", "quantity", "stop_loss", "take_profit", "entry_time", "confidence"}
        equity_curve: list[float] = [cash]
        trades: list[Trade] = []
        consecutive_losses = 0
        max_consecutive_losses = 0

        # Replay candle by candle
        for i, candle in enumerate(ohlcv_data):
            # Check stop loss / take profit on existing position
            if position:
                exit_reason = self._check_exit_conditions(position, candle)
                if exit_reason:
                    trade = self._close_position(
                        position, candle, exit_reason, token
                    )
                    cash += trade.pnl + (position["quantity"] * position["entry_price"])
                    trades.append(trade)

                    if trade.is_winner:
                        consecutive_losses = 0
                    else:
                        consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

                    position = None

            # Generate signals (if we have enough candles for indicators)
            if i < 14:  # Need minimum data for RSI/EMA
                equity_curve.append(cash if not position else self._mark_to_market(position, candle))
                continue

            # Build recent window for signal generation
            recent_data = ohlcv_data[max(0, i - 100):i + 1]

            # Generate signal using the provided signal generator
            try:
                signals = signal_generator.generate_signals_from_ohlcv(
                    recent_data, token, chain
                )
            except Exception as e:
                logger.debug(f"Signal generation error at bar {i}: {e}")
                signals = []

            # Combine signals (simple majority voting for backtest)
            combined = self._simple_combine(signals, token, chain)

            # If no position, check for entry
            if not position and combined and combined.confidence > 0.55:
                if combined.signal_type == SignalType.BULLISH:
                    # Calculate risk assessment
                    risk = self._assess_risk(recent_data, combined.confidence)

                    if risk.risk_score < 0.7:  # Risk acceptable
                        position = self._open_position(
                            cash, candle, combined, risk
                        )
                        cash -= position["quantity"] * candle.close * (1 + self.commission_pct + self.slippage_pct)

            # Mark to market
            if position:
                equity = self._mark_to_market(position, candle)
            else:
                equity = cash
            equity_curve.append(equity)

        # Close any open position at end of data
        if position:
            last_candle = ohlcv_data[-1]
            trade = self._close_position(position, last_candle, "end_of_data", token)
            cash += trade.pnl + (position["quantity"] * position["entry_price"])
            trades.append(trade)

        # Compute final result
        result = BacktestResult(
            token=token,
            chain=chain,
            start_date=ohlcv_data[0].timestamp,
            end_date=ohlcv_data[-1].timestamp,
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
        )

        self._compute_metrics(result, max_consecutive_losses)
        return result

    def _simple_combine(
        self,
        signals: list[Signal],
        token: str,
        chain: Chain,
    ) -> Optional[CombinedSignal]:
        """Simple signal combination for backtesting."""
        if not signals:
            return None

        bullish_score = sum(s.confidence for s in signals if s.signal_type == SignalType.BULLISH)
        bearish_score = sum(s.confidence for s in signals if s.signal_type == SignalType.BEARISH)

        if bullish_score > bearish_score and bullish_score > 0:
            return CombinedSignal(
                token=token,
                chain=chain,
                signal_type=SignalType.BULLISH,
                confidence=bullish_score / len(signals),
            )
        elif bearish_score > bullish_score and bearish_score > 0:
            return CombinedSignal(
                token=token,
                chain=chain,
                signal_type=SignalType.BEARISH,
                confidence=bearish_score / len(signals),
            )
        else:
            return CombinedSignal(
                token=token,
                chain=chain,
                signal_type=SignalType.NEUTRAL,
                confidence=0.0,
            )

    def _check_exit_conditions(self, position: dict, candle: OHLCV) -> Optional[str]:
        """Check if position should be closed."""
        # Stop loss
        if candle.low <= position["stop_loss"]:
            return "stop_loss"
        # Take profit
        if candle.high >= position["take_profit"]:
            return "take_profit"
        return None

    def _open_position(
        self,
        cash: float,
        candle: OHLCV,
        signal: CombinedSignal,
        risk: RiskAssessment,
    ) -> dict:
        """Open a new position."""
        # Position sizing
        position_value = cash * self.position_size_pct
        entry_price = candle.close * (1 + self.slippage_pct)
        quantity = position_value / entry_price

        # Stop loss from risk assessment or default
        sl_pct = risk.suggested_stop_loss_pct or self.stop_loss_pct
        stop_loss = entry_price * (1 - sl_pct)

        # Take profit at reward/risk ratio
        risk_distance = entry_price - stop_loss
        take_profit = entry_price + (risk_distance * self.take_profit_ratio)

        return {
            "entry_price": entry_price,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "entry_time": candle.timestamp,
            "confidence": signal.confidence,
        }

    def _close_position(
        self,
        position: dict,
        candle: OHLCV,
        reason: str,
        token: str,
    ) -> Trade:
        """Close position and create Trade record."""
        # Determine exit price based on reason
        if reason == "stop_loss":
            exit_price = position["stop_loss"]
        elif reason == "take_profit":
            exit_price = position["take_profit"]
        else:
            exit_price = candle.close * (1 - self.slippage_pct)

        # Apply commission
        exit_price *= (1 - self.commission_pct)

        entry_value = position["entry_price"] * position["quantity"]
        exit_value = exit_price * position["quantity"]
        pnl = exit_value - entry_value
        pnl_pct = pnl / entry_value if entry_value > 0 else 0

        return Trade(
            entry_time=position["entry_time"],
            exit_time=candle.timestamp,
            token=token,
            direction="LONG",
            entry_price=position["entry_price"],
            exit_price=exit_price,
            quantity=position["quantity"],
            pnl=pnl,
            pnl_pct=pnl_pct,
            signal_confidence=position["confidence"],
            exit_reason=reason,
        )

    def _mark_to_market(self, position: dict, candle: OHLCV) -> float:
        """Get current portfolio value with open position."""
        return position["quantity"] * candle.close

    def _assess_risk(self, recent_data: list[OHLCV], confidence: float) -> RiskAssessment:
        """Simple risk assessment for backtest."""
        closes = [c.close for c in recent_data[-20:]]
        if len(closes) < 2:
            return RiskAssessment(risk_score=0.5, suggested_stop_loss_pct=self.stop_loss_pct)

        # Volatility-based risk
        returns = np.diff(closes) / closes[:-1]
        volatility = np.std(returns) if len(returns) > 1 else 0.02

        # ATR-based stop loss
        if len(recent_data) >= 14:
            recent_ohlcv = recent_data[-14:]
            trs = []
            for j in range(1, len(recent_ohlcv)):
                high = recent_ohlcv[j].high
                low = recent_ohlcv[j].low
                prev_close = recent_ohlcv[j - 1].close
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            atr = np.mean(trs) if trs else closes[-1] * 0.02
            sl_pct = max(1.5 * atr / closes[-1], 0.02)  # 1.5x ATR or 2% minimum
        else:
            sl_pct = self.stop_loss_pct

        risk_score = min(volatility * 10, 1.0)  # Scale volatility to 0-1

        return RiskAssessment(
            risk_score=risk_score,
            suggested_stop_loss_pct=sl_pct,
        )

    def _compute_metrics(self, result: BacktestResult, max_consecutive_losses: int) -> None:
        """Compute all performance metrics from backtest results."""
        if not result.equity_curve:
            return

        # Total return
        final_value = result.equity_curve[-1]
        result.total_return_pct = (final_value / result.initial_capital - 1) * 100

        # Annualized return
        days = (result.end_date - result.start_date).days or 1
        years = days / 365.25
        if years > 0 and final_value > 0:
            result.annualized_return_pct = ((final_value / result.initial_capital) ** (1 / years) - 1) * 100

        # Trade statistics
        result.total_trades = len(result.trades)
        result.winning_trades = sum(1 for t in result.trades if t.is_winner)
        result.losing_trades = result.total_trades - result.winning_trades
        result.win_rate = result.winning_trades / result.total_trades if result.total_trades > 0 else 0

        # Average trade PnL
        if result.trades:
            result.avg_trade_pnl_pct = np.mean([t.pnl_pct for t in result.trades])
            result.avg_holding_period_bars = np.mean([
                (t.exit_time - t.entry_time).total_seconds() / 3600
                for t in result.trades
            ])
            result.avg_confidence = np.mean([t.signal_confidence for t in result.trades])

        # Profit factor
        gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in result.trades if t.pnl < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Drawdown
        equity = np.array(result.equity_curve)
        running_max = np.maximum.accumulate(equity)
        drawdown = (equity - running_max) / running_max * 100
        result.max_drawdown_pct = abs(drawdown.min())
        result.drawdown_curve = drawdown.tolist()

        # Sharpe ratio (assume risk-free rate = 0 for crypto)
        if len(equity) > 1:
            returns = np.diff(equity) / equity[:-1]
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            # Annualize (assuming hourly bars → 8760 bars/year)
            bars_per_year = 8760
            result.sharpe_ratio = (mean_return / std_return * np.sqrt(bars_per_year)) if std_return > 0 else 0

            # Sortino ratio (downside deviation only)
            downside_returns = returns[returns < 0]
            downside_std = np.std(downside_returns) if len(downside_returns) > 0 else std_return
            result.sortino_ratio = (mean_return / downside_std * np.sqrt(bars_per_year)) if downside_std > 0 else 0

        # Calmar ratio (annualized return / max drawdown)
        result.calmar_ratio = (
            result.annualized_return_pct / result.max_drawdown_pct
            if result.max_drawdown_pct > 0 else 0
        )

        result.max_consecutive_losses = max_consecutive_losses
