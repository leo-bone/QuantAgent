"""Unit tests for signal combiner."""

import pytest

from quantagent.common.models import Chain, Signal, SignalSource, SignalType
from quantagent.signal.combiner import SignalCombiner, DEFAULT_WEIGHTS


def _make_signal(
    source: SignalSource = SignalSource.TECHNICAL,
    signal_type: SignalType = SignalType.BULLISH,
    confidence: float = 0.7,
    token: str = "SOL",
    chain: Chain = Chain.SOLANA,
) -> Signal:
    """Helper to create a Signal for testing."""
    return Signal(
        source=source,
        signal_type=signal_type,
        token=token,
        chain=chain,
        confidence=confidence,
        indicator="test",
        value=42.0,
        description="test signal",
    )


class TestSignalCombiner:
    def test_all_bullish_gives_bullish(self):
        """When all signals are bullish, combined should be bullish."""
        combiner = SignalCombiner()
        tech = [_make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.8)]
        onchain = [_make_signal(SignalSource.ONCHAIN, SignalType.BULLISH, 0.7)]
        llm = _make_signal(SignalSource.LLM, SignalType.BULLISH, 0.6)

        result = combiner.combine("SOL", Chain.SOLANA, tech, onchain, llm)
        assert result.signal_type == SignalType.BULLISH
        assert result.confidence > 0.5

    def test_all_bearish_gives_bearish(self):
        """When all signals are bearish, combined should be bearish."""
        combiner = SignalCombiner()
        tech = [_make_signal(SignalSource.TECHNICAL, SignalType.BEARISH, 0.8)]
        onchain = [_make_signal(SignalSource.ONCHAIN, SignalType.BEARISH, 0.7)]

        result = combiner.combine("BNB", Chain.BSC, tech, onchain)
        assert result.signal_type == SignalType.BEARISH

    def test_no_signals_gives_neutral(self):
        """With no signals, result should be neutral with 0 confidence."""
        combiner = SignalCombiner()
        result = combiner.combine("SOL", Chain.SOLANA, [], [])
        assert result.signal_type == SignalType.NEUTRAL
        assert result.confidence == 0.0

    def test_conflicting_signals_reduces_confidence(self):
        """When signals conflict, confidence should be lower than unanimous."""
        combiner = SignalCombiner()
        # Unanimous bullish
        result_unanimous = combiner.combine(
            "SOL", Chain.SOLANA,
            [_make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.8)],
            [_make_signal(SignalSource.ONCHAIN, SignalType.BULLISH, 0.8)],
        )
        # Conflicting
        result_conflict = combiner.combine(
            "SOL", Chain.SOLANA,
            [_make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.8)],
            [_make_signal(SignalSource.ONCHAIN, SignalType.BEARISH, 0.8)],
        )
        # Conflicting should have lower confidence
        assert result_conflict.confidence < result_unanimous.confidence

    def test_onchain_weight_higher_than_technical(self):
        """On-chain signals should have higher default weight than technical."""
        assert DEFAULT_WEIGHTS[SignalSource.ONCHAIN] > DEFAULT_WEIGHTS[SignalSource.TECHNICAL]

    def test_strong_onchain_overrides_weak_technical(self):
        """A strong on-chain signal should override a weak technical signal."""
        combiner = SignalCombiner()
        weak_bullish = [_make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.3)]
        strong_bearish = [_make_signal(SignalSource.ONCHAIN, SignalType.BEARISH, 0.9)]

        result = combiner.combine("SOL", Chain.SOLANA, weak_bullish, strong_bearish)
        assert result.signal_type == SignalType.BEARISH

    def test_llm_signal_stored_in_result(self):
        """LLM signal should be stored in the combined signal."""
        combiner = SignalCombiner()
        llm = _make_signal(SignalSource.LLM, SignalType.BULLISH, 0.6)
        result = combiner.combine("SOL", Chain.SOLANA, [], [], llm)
        assert result.llm_signal is not None
        assert result.llm_signal.source == SignalSource.LLM

    def test_custom_weights(self):
        """Custom weights should override defaults."""
        custom = {SignalSource.TECHNICAL: 0.5, SignalSource.ONCHAIN: 0.2, SignalSource.LLM: 0.3}
        combiner = SignalCombiner(weights=custom)
        assert combiner.weights[SignalSource.TECHNICAL] == 0.5

    def test_multiple_technical_signals(self):
        """Multiple technical signals should be combined correctly."""
        combiner = SignalCombiner()
        tech = [
            _make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.7),
            _make_signal(SignalSource.TECHNICAL, SignalType.BULLISH, 0.6),
        ]
        result = combiner.combine("SOL", Chain.SOLANA, tech, [])
        assert result.signal_type == SignalType.BULLISH
        assert len(result.technical_signals) == 2
