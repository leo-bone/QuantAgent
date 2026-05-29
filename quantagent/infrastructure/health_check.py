"""Health checker — monitors external service availability.

Periodically checks the health of external APIs and services,
integrating with circuit breakers for automatic failover.

Usage:
    checker = HealthChecker()
    checker.register("jupiter", "https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000000", interval=30)
    await checker.start()  # Starts background health checks
    status = checker.get_all_status()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx
from loguru import logger

from quantagent.infrastructure.circuit_breaker import CircuitBreaker, get_breaker


@dataclass
class ServiceHealth:
    """Health status of an external service."""
    name: str
    url: str
    is_healthy: bool = False
    last_check: Optional[float] = None
    last_latency_ms: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_checks: int = 0
    total_failures: int = 0
    uptime_pct: float = 0.0
    error_message: Optional[str] = None


class HealthChecker:
    """Monitors health of external API services.

    Integrates with CircuitBreaker to automatically open circuits
    when services become unhealthy.
    """

    def __init__(self, default_interval: float = 30.0, timeout: float = 10.0):
        """
        Args:
            default_interval: Seconds between health checks
            timeout: HTTP timeout for health check requests
        """
        self.default_interval = default_interval
        self.timeout = timeout
        self._services: dict[str, ServiceHealth] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._check_tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None

    def register(
        self,
        name: str,
        url: str,
        interval: Optional[float] = None,
        breaker: Optional[CircuitBreaker] = None,
        check_fn: Optional[Callable] = None,
    ) -> None:
        """Register a service for health monitoring.

        Args:
            name: Service identifier
            url: Health check URL
            interval: Check interval in seconds (default: self.default_interval)
            breaker: Optional circuit breaker to integrate with
            check_fn: Optional custom health check function (async)
        """
        self._services[name] = ServiceHealth(name=name, url=url)
        if breaker:
            self._breakers[name] = breaker
        else:
            self._breakers[name] = get_breaker(name)
        self._intervals = getattr(self, '_intervals', {})
        self._intervals[name] = interval or self.default_interval
        self._check_fns = getattr(self, '_check_fns', {})
        self._check_fns[name] = check_fn

    async def start(self) -> None:
        """Start background health check tasks."""
        if self._running:
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=self.timeout, proxy=None)

        for name in self._services:
            self._check_tasks[name] = asyncio.create_task(
                self._check_loop(name)
            )

        logger.info(f"[HealthChecker] Started monitoring {len(self._services)} services")

    async def stop(self) -> None:
        """Stop all health check tasks."""
        self._running = False
        for task in self._check_tasks.values():
            task.cancel()
        self._check_tasks.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_now(self, name: str) -> ServiceHealth:
        """Immediately check a specific service."""
        if name not in self._services:
            raise ValueError(f"Service '{name}' not registered")

        await self._perform_check(name)
        return self._services[name]

    def get_status(self, name: str) -> ServiceHealth:
        """Get current health status of a service."""
        return self._services.get(name)

    def get_all_status(self) -> dict[str, dict]:
        """Get health status of all monitored services."""
        result = {}
        for name, health in self._services.items():
            result[name] = {
                "healthy": health.is_healthy,
                "latency_ms": health.last_latency_ms,
                "uptime_pct": round(health.uptime_pct, 1),
                "last_check": health.last_check,
                "consecutive_failures": health.consecutive_failures,
                "breaker_state": self._breakers[name].state.value if name in self._breakers else None,
            }
            # Also include breaker status
            if name in self._breakers:
                result[name]["breaker"] = self._breakers[name].get_status()
        return result

    async def _check_loop(self, name: str) -> None:
        """Background loop for periodic health checks."""
        interval = self._intervals.get(name, self.default_interval)
        while self._running:
            try:
                await self._perform_check(name)
            except Exception as e:
                logger.debug(f"[HealthChecker:{name}] Check error: {e}")
            await asyncio.sleep(interval)

    async def _perform_check(self, name: str) -> None:
        """Execute a single health check."""
        service = self._services[name]
        breaker = self._breakers.get(name)
        check_fn = getattr(self, '_check_fns', {}).get(name)

        start_time = time.time()
        try:
            if check_fn:
                # Custom health check function
                await check_fn()
            elif self._client:
                # Default HTTP health check
                resp = await self._client.get(service.url)
                resp.raise_for_status()

            # Success
            latency = time.time() - start_time
            service.is_healthy = True
            service.last_check = time.time()
            service.last_latency_ms = latency * 1000
            service.consecutive_failures = 0
            service.consecutive_successes += 1
            service.total_checks += 1
            service.error_message = None

            # Update uptime
            if service.total_checks > 0:
                service.uptime_pct = (
                    (service.total_checks - service.total_failures) / service.total_checks * 100
                )

            # Record success in circuit breaker
            if breaker:
                breaker.record_success(latency)

        except Exception as e:
            # Failure
            service.is_healthy = False
            service.last_check = time.time()
            service.consecutive_failures += 1
            service.consecutive_successes = 0
            service.total_checks += 1
            service.total_failures += 1
            service.error_message = str(e)

            # Update uptime
            if service.total_checks > 0:
                service.uptime_pct = (
                    (service.total_checks - service.total_failures) / service.total_checks * 100
                )

            # Record failure in circuit breaker
            if breaker:
                breaker.record_failure(e)

            logger.debug(
                f"[HealthChecker:{name}] Check failed: {e} "
                f"(consecutive: {service.consecutive_failures})"
            )
