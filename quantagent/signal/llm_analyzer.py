"""LLM-powered signal analyzer.

Uses OpenAI/Claude to analyze market data and generate trading signals
with reasoning. Falls back gracefully when API keys are not available.

Uses async SDK clients to avoid blocking the event loop.
Integrates with circuit breaker for resilience.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from quantagent.common.models import (
    Chain,
    Signal,
    SignalSource,
    SignalType,
)
from quantagent.signal.base import SignalGenerator
from quantagent.infrastructure.circuit_breaker import get_breaker, CircuitOpenError


SYSTEM_PROMPT = """You are a cryptocurrency trading analyst AI. Analyze the provided market data and generate a trading signal.

You MUST respond with valid JSON matching this exact schema:
{
    "signal_type": "bullish" | "bearish" | "neutral",
    "confidence": 0.0 to 1.0,
    "reasoning": "Your detailed analysis in 2-4 sentences",
    "key_factors": ["factor1", "factor2", ...],
    "risk_level": "low" | "medium" | "high"
}

Analysis guidelines:
- Consider technical indicators, on-chain activity, and market sentiment together
- Weight on-chain signals (whale movements, liquidity changes) heavily
- Be cautious with conflicting signals - lower confidence when uncertain
- Consider the broader market context (BTC correlation, DeFi sentiment)
- Factor in the specific chain's characteristics (Solana = speed/memes, BSC = DeFi/yield)
- Never give 1.0 confidence - there is always uncertainty in markets
"""


class LLMAnalyzer(SignalGenerator):
    """LLM-based market analyzer using async OpenAI or Claude.

    Key improvements over synchronous version:
    - Uses AsyncOpenAI / AsyncAnthropic to avoid blocking event loop
    - Integrates with CircuitBreaker for resilience
    - Adds timeout protection (30s default)
    - Tracks token usage for cost monitoring
    """

    source = "llm"

    def __init__(
        self,
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        model: str = "gpt-4o",
        max_tokens: int = 1000,
        timeout: float = 30.0,
    ):
        self.openai_api_key = openai_api_key
        self.anthropic_api_key = anthropic_api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._openai_client = None
        self._anthropic_client = None

        # Circuit breakers for each provider
        self._openai_breaker = get_breaker("openai", failure_threshold=3, recovery_timeout=120)
        self._anthropic_breaker = get_breaker("anthropic", failure_threshold=3, recovery_timeout=120)

        # Token usage tracking
        self._total_tokens = 0
        self._total_calls = 0
        self._total_errors = 0

    def _get_openai_client(self):
        """Lazy init async OpenAI client."""
        if self._openai_client is None and self.openai_api_key:
            try:
                from openai import AsyncOpenAI
                self._openai_client = AsyncOpenAI(
                    api_key=self.openai_api_key,
                    timeout=self.timeout,
                )
            except ImportError:
                logger.error("openai package not installed (pip install openai)")
        return self._openai_client

    def _get_anthropic_client(self):
        """Lazy init async Anthropic client."""
        if self._anthropic_client is None and self.anthropic_api_key:
            try:
                from anthropic import AsyncAnthropic
                self._anthropic_client = AsyncAnthropic(
                    api_key=self.anthropic_api_key,
                    timeout=self.timeout,
                )
            except ImportError:
                logger.error("anthropic package not installed (pip install anthropic)")
        return self._anthropic_client

    async def generate_signals(
        self,
        token: str,
        chain: Chain,
        market_context: Optional[dict] = None,
    ) -> list[Signal]:
        """Generate LLM-powered signals.

        Args:
            token: Token symbol
            chain: Blockchain
            market_context: Dict containing:
                - technical_summary: str (from TechnicalSignalGenerator)
                - onchain_events: str (from on-chain monitoring)
                - fear_greed_index: int
                - funding_rates: dict
                - price_usd: float
                - volume_24h: float
                - price_change_24h: float
        """
        if not market_context:
            logger.warning(f"No market context provided for LLM analysis of {token}")
            return []

        prompt = self._build_prompt(token, chain, market_context)

        try:
            result = await self._call_llm(prompt)
            if not result:
                return []

            signal = self._parse_response(result, token, chain)
            if signal:
                return [signal]

        except CircuitOpenError as e:
            logger.warning(f"[LLM] Circuit breaker open: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[LLM] Analysis timed out for {token}")
            self._total_errors += 1
        except Exception as e:
            logger.error(f"LLM analysis failed for {token}: {e}")
            self._total_errors += 1

        return []

    def _build_prompt(self, token: str, chain: Chain, context: dict) -> str:
        """Build the analysis prompt from market context."""
        parts = [
            f"Analyze {token} on {chain.value.upper()} chain.",
            "",
            "## Market Data",
            f"- Current Price: ${context.get('price_usd', 'N/A')}",
            f"- 24h Change: {context.get('price_change_24h', 'N/A')}%",
            f"- 24h Volume: ${context.get('volume_24h', 'N/A'):,.0f}" if isinstance(context.get('volume_24h'), (int, float)) else f"- 24h Volume: {context.get('volume_24h', 'N/A')}",
        ]

        if context.get("technical_summary"):
            parts.extend(["", "## Technical Analysis", context["technical_summary"]])

        if context.get("onchain_events"):
            parts.extend(["", "## On-Chain Activity", context["onchain_events"]])

        if context.get("fear_greed_index") is not None:
            parts.extend(["", "## Market Sentiment", f"Fear & Greed Index: {context['fear_greed_index']}"])

        if context.get("funding_rates"):
            parts.extend(["", "## Funding Rates", json.dumps(context["funding_rates"])])

        return "\n".join(parts)

    async def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM API using async clients with circuit breaker."""
        # Try OpenAI first (if model starts with gpt)
        if self.model.startswith("gpt") and self.openai_api_key:
            result = await self._call_openai(prompt)
            if result:
                return result

        # Try Anthropic (if model starts with claude, or as fallback)
        if (self.model.startswith("claude") or not self.model.startswith("gpt")) and self.anthropic_api_key:
            result = await self._call_anthropic(prompt)
            if result:
                return result

        # If model is gpt but OpenAI failed, try Anthropic as fallback
        if self.model.startswith("gpt") and self.anthropic_api_key:
            result = await self._call_anthropic(prompt)
            if result:
                return result

        logger.warning("No LLM API available or all failed")
        return None

    async def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API with circuit breaker protection."""
        if not self._openai_breaker.allow_request():
            logger.debug("[LLM] OpenAI circuit breaker open, skipping")
            return None

        client = self._get_openai_client()
        if not client:
            return None

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=self.max_tokens,
                    temperature=0.3,
                    response_format={"type": "json_object"},
                ),
                timeout=self.timeout,
            )

            self._openai_breaker.record_success()
            self._total_calls += 1

            # Track token usage
            if hasattr(response, 'usage') and response.usage:
                self._total_tokens += response.usage.total_tokens

            return response.choices[0].message.content

        except asyncio.TimeoutError:
            self._openai_breaker.record_failure(TimeoutError("OpenAI timeout"))
            raise
        except Exception as e:
            self._openai_breaker.record_failure(e)
            logger.error(f"OpenAI API error: {e}")
            return None

    async def _call_anthropic(self, prompt: str) -> Optional[str]:
        """Call Anthropic API with circuit breaker protection."""
        if not self._anthropic_breaker.allow_request():
            logger.debug("[LLM] Anthropic circuit breaker open, skipping")
            return None

        client = self._get_anthropic_client()
        if not client:
            return None

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self.model if self.model.startswith("claude") else "claude-3-5-sonnet-20241022",
                    max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self.timeout,
            )

            self._anthropic_breaker.record_success()
            self._total_calls += 1

            # Track token usage
            if hasattr(response, 'usage') and response.usage:
                self._total_tokens += response.usage.input_tokens + response.usage.output_tokens

            return response.content[0].text

        except asyncio.TimeoutError:
            self._anthropic_breaker.record_failure(TimeoutError("Anthropic timeout"))
            raise
        except Exception as e:
            self._anthropic_breaker.record_failure(e)
            logger.error(f"Anthropic API error: {e}")
            return None

    def _parse_response(self, response: str, token: str, chain: Chain) -> Optional[Signal]:
        """Parse the LLM response into a Signal."""
        try:
            data = json.loads(response)

            signal_type_str = data.get("signal_type", "neutral").lower()
            signal_type = {
                "bullish": SignalType.BULLISH,
                "bearish": SignalType.BEARISH,
            }.get(signal_type_str, SignalType.NEUTRAL)

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            reasoning = data.get("reasoning", "")
            key_factors = data.get("key_factors", [])
            description = reasoning
            if key_factors:
                description += f" | Key factors: {', '.join(key_factors)}"

            return Signal(
                source=SignalSource.LLM,
                signal_type=signal_type,
                token=token,
                chain=chain,
                confidence=confidence,
                indicator="llm_analysis",
                value={
                    "reasoning": reasoning,
                    "key_factors": key_factors,
                    "risk_level": data.get("risk_level", "medium"),
                },
                description=description,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to parse LLM response: {e}")
            logger.debug(f"Raw response: {response[:200]}")
            return None

    async def is_available(self) -> bool:
        """Check if any LLM API is available (and circuits are not open)."""
        openai_ok = bool(self.openai_api_key) and self._openai_breaker.state.value != "open"
        anthropic_ok = bool(self.anthropic_api_key) and self._anthropic_breaker.state.value != "open"
        return openai_ok or anthropic_ok

    def get_usage_stats(self) -> dict:
        """Get token usage and call statistics."""
        return {
            "total_tokens": self._total_tokens,
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "error_rate": self._total_errors / max(self._total_calls, 1),
            "openai_breaker": self._openai_breaker.get_status(),
            "anthropic_breaker": self._anthropic_breaker.get_status(),
        }
