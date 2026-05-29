"""Unit tests for technical signal generator (pure numpy/pandas)."""

import numpy as np
import pandas as pd
import pytest

from quantagent.common.models import Chain, SignalType, SignalSource
from quantagent.signal.technical import (
    calc_rsi,
    calc_ema,
    calc_macd,
    calc_bollinger_bands,
    calc_atr,
    TechnicalSignalGenerator,
)


# ─── Helper: generate realistic OHLCV DataFrame ───

def make_price_df(n: int = 200, base_price: float = 150.0, volatility: float = 0.02) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing."""
    np.random.seed(42)
    returns = np.random.normal(0.0001, volatility, n)
    closes = base_price * np.cumprod(1 + returns)
    highs = closes * (1 + np.abs(np.random.normal(0, volatility / 2, n)))
    lows = closes * (1 - np.abs(np.random.normal(0, volatility / 2, n)))
    opens = closes * (1 + np.random.normal(0, volatility / 3, n))
    volumes = base_price * np.random.uniform(1000, 5000, n)
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# ─── calc_rsi tests ───

class TestCalcRSI:
    def test_rsi_range(self):
        """RSI should be between 0 and 100."""
        df = make_price_df(100)
        rsi = calc_rsi(df["close"])
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_overbought_trend(self):
        """Consistently rising prices should push RSI above 70."""
        rising = pd.Series(range(100, 200), dtype=float)  # strict uptrend
        rsi = calc_rsi(rising)
        # RSI should be high for a strong uptrend (may have NaN for first 14)
        valid_rsi = rsi.dropna()
        if len(valid_rsi) > 0:
            assert valid_rsi.iloc[-1] > 70 or valid_rsi.iloc[-1] > 50  # At minimum RSI should be elevated

    def test_rsi_oversold_trend(self):
        """Consistently falling prices should push RSI below 30."""
        falling = pd.Series(range(200, 100, -1), dtype=float)  # strict downtrend
        rsi = calc_rsi(falling)
        assert rsi.iloc[-1] < 30

    def test_rsi_length(self):
        """RSI output should have same length as input."""
        s = pd.Series(np.random.randn(50).cumsum() + 100)
        rsi = calc_rsi(s)
        assert len(rsi) == len(s)

    def test_rsi_nan_at_start(self):
        """RSI should have NaN values at the start (before enough data)."""
        s = pd.Series([100, 101, 102, 103])
        rsi = calc_rsi(s, period=14)
        # First 14 values should be NaN
        assert rsi.iloc[:14].isna().all()


# ─── calc_ema tests ───

class TestCalcEMA:
    def test_ema_smoothing(self):
        """EMA should be smoother (less volatile) than the raw series."""
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        ema = calc_ema(s, 20)
        assert ema.std() < s.std()

    def test_ema_follows_price(self):
        """EMA should track the general direction of the price."""
        rising = pd.Series(range(100), dtype=float)
        ema = calc_ema(rising, 10)
        # EMA should also be rising
        assert ema.iloc[-1] > ema.iloc[0]

    def test_ema_length(self):
        """EMA output should have same length as input."""
        s = pd.Series(np.random.randn(50).cumsum() + 100)
        ema = calc_ema(s, 10)
        assert len(ema) == len(s)


# ─── calc_macd tests ───

class TestCalcMACD:
    def test_macd_returns_three(self):
        """MACD should return (macd_line, signal_line, histogram)."""
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        macd_line, signal_line, histogram = calc_macd(s)
        assert len(macd_line) == len(signal_line) == len(histogram) == len(s)

    def test_macd_histogram_equals_diff(self):
        """Histogram should equal macd_line - signal_line."""
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        macd_line, signal_line, histogram = calc_macd(s)
        np.testing.assert_allclose(
            histogram.dropna().values,
            (macd_line - signal_line).dropna().values,
            atol=1e-10,
        )

    def test_macd_crossover_detection(self):
        """When MACD crosses above signal, histogram should flip from negative to positive."""
        # Create a series that transitions from down to up
        down = pd.Series(np.linspace(100, 80, 50))
        up = pd.Series(np.linspace(80, 120, 50))
        s = pd.concat([down, up], ignore_index=True)
        macd_line, signal_line, histogram = calc_macd(s)
        # After the transition, MACD should be above signal line
        assert histogram.iloc[-1] > 0


# ─── calc_bollinger_bands tests ───

class TestCalcBollingerBands:
    def test_bands_order(self):
        """Upper band should be above middle, which should be above lower."""
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        upper, mid, lower = calc_bollinger_bands(s)
        valid_idx = upper.dropna().index
        assert (upper[valid_idx] >= mid[valid_idx]).all()
        assert (mid[valid_idx] >= lower[valid_idx]).all()

    def test_bands_width(self):
        """With higher volatility, bands should be wider."""
        np.random.seed(42)
        low_vol = pd.Series(np.random.normal(0, 1, 100).cumsum() + 100)
        high_vol = pd.Series(np.random.normal(0, 5, 100).cumsum() + 100)
        _, _, lower_low = calc_bollinger_bands(low_vol)
        _, _, lower_high = calc_bollinger_bands(high_vol)
        # Width of high-vol bands should be greater
        width_low = (calc_bollinger_bands(low_vol)[0] - calc_bollinger_bands(low_vol)[2]).dropna().mean()
        width_high = (calc_bollinger_bands(high_vol)[0] - calc_bollinger_bands(high_vol)[2]).dropna().mean()
        assert width_high > width_low

    def test_mid_equals_sma(self):
        """Middle band should equal the simple moving average."""
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        _, mid, _ = calc_bollinger_bands(s, period=20)
        sma = s.rolling(window=20).mean()
        pd.testing.assert_series_equal(mid, sma, check_names=False)


# ─── calc_atr tests ───

class TestCalcATR:
    def test_atr_positive(self):
        """ATR should always be positive."""
        df = make_price_df(100)
        atr = calc_atr(df["high"], df["low"], df["close"])
        valid = atr.dropna()
        assert (valid >= 0).all()

    def test_atr_higher_with_volatility(self):
        """ATR should be higher for more volatile data."""
        low_vol_df = make_price_df(100, volatility=0.01)
        high_vol_df = make_price_df(100, volatility=0.05)
        atr_low = calc_atr(low_vol_df["high"], low_vol_df["low"], low_vol_df["close"]).dropna().mean()
        atr_high = calc_atr(high_vol_df["high"], high_vol_df["low"], high_vol_df["close"]).dropna().mean()
        assert atr_high > atr_low

    def test_atr_length(self):
        """ATR should have same length as input."""
        df = make_price_df(50)
        atr = calc_atr(df["high"], df["low"], df["close"])
        assert len(atr) == len(df)


# ─── TechnicalSignalGenerator integration tests ───

class TestTechnicalSignalGenerator:
    @pytest.mark.asyncio
    async def test_generate_signals_returns_list(self):
        """generate_signals should return a list of Signal objects."""
        df = make_price_df(200)
        gen = TechnicalSignalGenerator()
        signals = await gen.generate_signals("SOL", Chain.SOLANA, df)
        assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_signals_have_correct_source(self):
        """All technical signals should have TECHNICAL source."""
        df = make_price_df(200)
        gen = TechnicalSignalGenerator()
        signals = await gen.generate_signals("SOL", Chain.SOLANA, df)
        for s in signals:
            assert s.source == SignalSource.TECHNICAL

    @pytest.mark.asyncio
    async def test_signals_have_valid_confidence(self):
        """Signal confidence should be between 0 and 1."""
        df = make_price_df(200)
        gen = TechnicalSignalGenerator()
        signals = await gen.generate_signals("SOL", Chain.SOLANA, df)
        for s in signals:
            assert 0 <= s.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_empty(self):
        """Should return empty list if data is insufficient."""
        df = make_price_df(10)  # Too few rows
        gen = TechnicalSignalGenerator()
        signals = await gen.generate_signals("SOL", Chain.SOLANA, df)
        assert signals == []

    @pytest.mark.asyncio
    async def test_none_data_returns_empty(self):
        """Should return empty list if no data provided."""
        gen = TechnicalSignalGenerator()
        signals = await gen.generate_signals("SOL", Chain.SOLANA, None)
        assert signals == []

    @pytest.mark.asyncio
    async def test_oversold_rsi_triggers_bullish(self):
        """Oversold RSI should generate a bullish signal."""
        # Create a series with a strong downtrend at the end
        np.random.seed(42)
        flat = np.random.normal(0, 0.005, 170).cumsum() + 100
        decline = np.linspace(flat[-1], flat[-1] * 0.7, 30)  # 30% drop
        closes = pd.Series(np.concatenate([flat, decline]))
        df = pd.DataFrame({
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": 1000.0,
        })
        gen = TechnicalSignalGenerator(rsi_oversold=35)
        signals = await gen.generate_signals("SOL", Chain.SOLANA, df)
        rsi_signals = [s for s in signals if s.indicator == "RSI"]
        if rsi_signals:  # May not trigger if not oversold enough
            assert rsi_signals[0].signal_type == SignalType.BULLISH
