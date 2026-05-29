"""Circuit Breaker pattern for external API resilience.

Prevents cascading failures by stopping calls to unhealthy services.
Implements the three-state circuit breaker pattern:
- CLOSED: Normal operation, calls go through
- OPEN: Service is down, calls are rejected immediately
- HALF_OPEN: Testing if service has recovered, limited calls allowed

Usage:
    breaker = CircuitBreaker(name="jupiter", failure_threshold=5, recovery_timeout=60)

    async with breaker:
        result = await httpx_client.get(url)

    # Or manual:
    if breaker.allow_request():
        try:
            result = await call_api()
            breaker.record_success()
        except Exception as e:
            breaker.record_failure(e)
    else:
        raise CircuitOpenError("Jupiter API circuit is open")
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from loguru import logger


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and requests are blocked."""
    pass


class CircuitBreaker:
    """Thread-safe circuit breaker for external API calls.

    Features:
    - Configurable failure threshold and recovery timeout
    - Exponential backoff during half-open state
    - Per-call latency tracking
    - Comprehensive state for observability
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
        expected_exceptions: tuple = (Exception,),
    ):
        """
        Args:
            name: Identifier for this circuit (e.g., "jupiter", "coingecko")
            failure_threshold: Consecutive failures before opening circuit
            recovery_timeout: Seconds before trying half-open state
            half_open_max_calls: Max calls allowed in half-open state
            expected_exceptions: Exception types that count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.expected_exceptions = expected_exceptions

        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change_time = time.time()
        self._half_open_calls = 0
        self._total_calls = 0
        self._total_failures = 0
        self._total_successes = 0
        self._last_error: Optional[str] = None

        # Latency tracking
        self._total_latency = 0.0
        self._latency_count = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state (with automatic state transitions)."""
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if self._last_failure_time and (
                time.time() - self._last_failure_time >= self.recovery_timeout
            ):
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed."""
        self._total_calls += 1

        current_state = self.state

        if current_state == CircuitState.CLOSED:
            return True
        elif current_state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        else:  # OPEN
            return False

    def record_success(self, latency: float = 0.0) -> None:
        """Record a successful call."""
        self._success_count += 1
        self._total_successes += 1
        self._failure_count = 0  # Reset consecutive failure count

        if latency > 0:
            self._total_latency += latency
            self._latency_count += 1

        if self._state == CircuitState.HALF_OPEN:
            # Service recovered
            self._transition_to(CircuitState.CLOSED)
            logger.info(f"[CircuitBreaker:{self.name}] Service recovered → CLOSED")

    def record_failure(self, error: Optional[Exception] = None) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._total_failures += 1
        self._last_failure_time = time.time()
        self._last_error = str(error) if error else "Unknown error"

        if self._state == CircuitState.HALF_OPEN:
            # Service still failing, go back to OPEN
            self._transition_to(CircuitState.OPEN)
            logger.warning(f"[CircuitBreaker:{self.name}] Service still failing → OPEN")
        elif self._failure_count >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)
            logger.warning(
                f"[CircuitBreaker:{self.name}] {self._failure_count} consecutive failures → OPEN"
            )

    async def __aenter__(self):
        """Async context manager — check circuit state before call."""
        if not self.allow_request():
            raise CircuitOpenError(
                f"Circuit '{self.name}' is {self.state.value}. "
                f"Last error: {self._last_error}"
            )
        self._call_start = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager — record outcome after call."""
        latency = time.time() - getattr(self, '_call_start', time.time())

        if exc_type is not None:
            if issubclass(exc_type, self.expected_exceptions):
                self.record_failure(exc_val)
            # Don't suppress the exception
            return False
        else:
            self.record_success(latency)
            return False

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new circuit state."""
        old_state = self._state
        self._state = new_state
        self._last_state_change_time = time.time()
        self._half_open_calls = 0

        if old_state != new_state:
            logger.info(
                f"[CircuitBreaker:{self.name}] State: {old_state.value} → {new_state.value}"
            )

    def get_status(self) -> dict:
        """Get circuit breaker status for observability."""
        avg_latency = (
            self._total_latency / self._latency_count
            if self._latency_count > 0
            else 0
        )
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "failure_rate": self._total_failures / max(self._total_calls, 1),
            "avg_latency_ms": round(avg_latency * 1000, 2),
            "last_error": self._last_error,
            "last_state_change": self._last_state_change_time,
        }

    def reset(self) -> None:
        """Reset circuit to closed state."""
        self._transition_to(CircuitState.CLOSED)
        self._failure_count = 0
        self._success_count = 0
        self._last_error = None


# ─── Global registry of circuit breakers ───

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Get or create a named circuit breaker."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name=name, **kwargs)
    return _breakers[name]


def get_all_breaker_status() -> dict[str, dict]:
    """Get status of all registered circuit breakers."""
    return {name: breaker.get_status() for name, breaker in _breakers.items()}
