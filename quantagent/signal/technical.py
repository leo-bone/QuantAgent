"""Technical indicator signal generator.

Pure numpy/pandas implementation of RSI, MACD, Bollinger Bands, ATR, volume analysis.
No external TA library dependencies required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from quantagent.common.models import Chain, Signal, SignalSource, SignalType
from quantagent.signal.base import SignalGenerator


# ──────────────────────────────────────────────────────
#  Pure numpy/pandas indicator helper functions
# ──────────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI using Wilder's smoothing method.

    RSI = 100 - (100 / (1 + RS))
    RS  = avg_gain / avg_loss  (Wilder's EMA)
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing: first value is simple mean, rest is EMA with alpha=1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD line, Signal line, Histogram.

    Returns:
        (macd_line, signal_line, histogram)
    """
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands.

    Returns:
        (upper, mid, lower)
    """
    mid = series.rolling(window=period).mean()
    std = series.rolling(window=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def calc_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


# ──────────────────────────────────────────────────────
#  Signal Generator
# ──────────────────────────────────────────────────────

class TechnicalSignalGenerator(SignalGenerator):
    """Generates signals from technical indicators (pure numpy/pandas)."""

    source = "technical"

    def __init__(
        self,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_std: float = 2.0,
        bb_period: int = 20,
        volume_spike_threshold: float = 2.0,
    ):
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_std = bb_std
        self.bb_period = bb_period
        self.volume_spike_threshold = volume_spike_threshold

    async def generate_signals(
        self,
        token: str,
        chain: Chain,
        df: Optional[pd.DataFrame] = None,
    ) -> list[Signal]:
        """Generate technical signals from OHLCV data.

        Args:
            token: Token symbol
            chain: Blockchain
            df: DataFrame with columns: open, high, low, close, volume
                If None, will attempt to fetch data.
        """
        signals = []

        if df is None or len(df) < 30:
            logger.warning(f"Insufficient data for technical analysis of {token}")
            return signals

        try:
            # ─── RSI Signal ───
            rsi_signal = self._rsi_signal(df, token, chain)
            if rsi_signal:
                signals.append(rsi_signal)

            # ─── MACD Signal ───
            macd_signal = self._macd_signal(df, token, chain)
            if macd_signal:
                signals.append(macd_signal)

            # ─── Bollinger Bands Signal ───
            bb_signal = self._bollinger_signal(df, token, chain)
            if bb_signal:
                signals.append(bb_signal)

            # ─── Volume Spike Signal ───
            vol_signal = self._volume_signal(df, token, chain)
            if vol_signal:
                signals.append(vol_signal)

        except Exception as e:
            logger.error(f"Technical analysis failed for {token}: {e}")

        return signals

    def _rsi_signal(self, df: pd.DataFrame, token: str, chain: Chain) -> Optional[Signal]:
        """RSI-based signal (pure pandas)."""
        try:
            rsi = calc_rsi(df["close"], period=14)
            if rsi.empty:
                return None
            current_rsi = float(rsi.iloc[-1])
            if np.isnan(current_rsi):
                return None

            if current_rsi > self.rsi_overbought:
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BEARISH,
                    token=token,
                    chain=chain,
                    confidence=min((current_rsi - self.rsi_overbought) / 30, 1.0),
                    indicator="RSI",
                    value=current_rsi,
                    description=f"RSI overbought at {current_rsi:.1f}",
                )
            elif current_rsi < self.rsi_oversold:
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BULLISH,
                    token=token,
                    chain=chain,
                    confidence=min((self.rsi_oversold - current_rsi) / 30, 1.0),
                    indicator="RSI",
                    value=current_rsi,
                    description=f"RSI oversold at {current_rsi:.1f}",
                )
        except Exception as e:
            logger.debug(f"RSI calculation failed: {e}")
        return None

    def _macd_signal(self, df: pd.DataFrame, token: str, chain: Chain) -> Optional[Signal]:
        """MACD crossover signal (pure pandas)."""
        try:
            macd_line, signal_line, histogram = calc_macd(
                df["close"],
                fast=self.macd_fast,
                slow=self.macd_slow,
                signal=self.macd_signal,
            )
            if len(macd_line) < 2:
                return None

            prev_diff = float(macd_line.iloc[-2] - signal_line.iloc[-2])
            curr_diff = float(macd_line.iloc[-1] - signal_line.iloc[-1])

            # Skip if NaN
            if np.isnan(prev_diff) or np.isnan(curr_diff):
                return None

            if prev_diff <= 0 and curr_diff > 0:
                # Bullish crossover
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BULLISH,
                    token=token,
                    chain=chain,
                    confidence=min(abs(curr_diff) / df["close"].iloc[-1] * 100, 1.0),
                    indicator="MACD",
                    value=float(histogram.iloc[-1]),
                    description=f"MACD bullish crossover (histogram: {histogram.iloc[-1]:.4f})",
                )
            elif prev_diff >= 0 and curr_diff < 0:
                # Bearish crossover
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BEARISH,
                    token=token,
                    chain=chain,
                    confidence=min(abs(curr_diff) / df["close"].iloc[-1] * 100, 1.0),
                    indicator="MACD",
                    value=float(histogram.iloc[-1]),
                    description=f"MACD bearish crossover (histogram: {histogram.iloc[-1]:.4f})",
                )
        except Exception as e:
            logger.debug(f"MACD calculation failed: {e}")
        return None

    def _bollinger_signal(self, df: pd.DataFrame, token: str, chain: Chain) -> Optional[Signal]:
        """Bollinger Bands signal (pure pandas)."""
        try:
            upper, mid, lower = calc_bollinger_bands(
                df["close"],
                period=self.bb_period,
                num_std=self.bb_std,
            )
            if upper.empty or np.isnan(upper.iloc[-1]):
                return None

            current_price = float(df["close"].iloc[-1])
            upper_val = float(upper.iloc[-1])
            lower_val = float(lower.iloc[-1])

            if current_price > upper_val:
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BEARISH,
                    token=token,
                    chain=chain,
                    confidence=0.5,
                    indicator="BB",
                    value=current_price,
                    description=f"Price above upper Bollinger Band ({upper_val:.2f})",
                )
            elif current_price < lower_val:
                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=SignalType.BULLISH,
                    token=token,
                    chain=chain,
                    confidence=0.5,
                    indicator="BB",
                    value=current_price,
                    description=f"Price below lower Bollinger Band ({lower_val:.2f})",
                )
        except Exception as e:
            logger.debug(f"Bollinger Bands calculation failed: {e}")
        return None

    def _volume_signal(self, df: pd.DataFrame, token: str, chain: Chain) -> Optional[Signal]:
        """Volume spike detection (pure pandas)."""
        try:
            if len(df) < 20:
                return None

            avg_volume = float(df["volume"].iloc[-20:].mean())
            current_volume = float(df["volume"].iloc[-1])

            if avg_volume == 0:
                return None

            ratio = current_volume / avg_volume

            if ratio >= self.volume_spike_threshold:
                # Determine direction from price
                price_change = (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2]
                signal_type = SignalType.BULLISH if price_change > 0 else SignalType.BEARISH

                return Signal(
                    source=SignalSource.TECHNICAL,
                    signal_type=signal_type,
                    token=token,
                    chain=chain,
                    confidence=min((ratio - 1) / 3, 0.8),
                    indicator="VOLUME_SPIKE",
                    value=ratio,
                    description=f"Volume {ratio:.1f}x average (price {'up' if price_change > 0 else 'down'})",
                )
        except Exception as e:
            logger.debug(f"Volume analysis failed: {e}")
        return None

    async def is_available(self) -> bool:
        """Technical analysis is always available (no external dependencies)."""
        return True

    def generate_signals_from_ohlcv(
        self,
        ohlcv_data: list,
        token: str,
        chain: Chain,
    ) -> list[Signal]:
        """Generate signals directly from OHLCV data list (for backtesting).

        Args:
            ohlcv_data: List of OHLCV objects with timestamp, open, high, low, close, volume
            token: Token symbol
            chain: Blockchain

        Returns:
            List of Signal objects
        """
        if not ohlcv_data or len(ohlcv_data) < 30:
            return []

        # Convert OHLCV objects to DataFrame
        df = pd.DataFrame([{
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        } for c in ohlcv_data])

        # Run synchronously (backtest doesn't need async)
        signals = []
        try:
            rsi_signal = self._rsi_signal(df, token, chain)
            if rsi_signal:
                signals.append(rsi_signal)

            macd_signal = self._macd_signal(df, token, chain)
            if macd_signal:
                signals.append(macd_signal)

            bb_signal = self._bollinger_signal(df, token, chain)
            if bb_signal:
                signals.append(bb_signal)

            vol_signal = self._volume_signal(df, token, chain)
            if vol_signal:
                signals.append(vol_signal)
        except Exception as e:
            logger.debug(f"Technical signal generation from OHLCV failed: {e}")

        return signals
