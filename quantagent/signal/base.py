"""Signal engine base - abstract interface for all signal generators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from quantagent.common.models import Chain, Signal


class SignalGenerator(ABC):
    """Abstract base class for signal generators."""

    source: str  # Must be set by subclass

    @abstractmethod
    async def generate_signals(
        self,
        token: str,
        chain: Chain,
    ) -> list[Signal]:
        """Generate trading signals for a token on a chain."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this signal generator is available (e.g., API keys set)."""
        ...
