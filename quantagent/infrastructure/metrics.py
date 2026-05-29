"""Metrics collector — runtime observability for QuantAgent.

Tracks signal accuracy, trade PnL, API latency, system health,
and other key metrics. Thread-safe singleton pattern.

Usage:
    metrics = get_metrics()
    metrics.record_signal("SOL", "technical", "bullish", 0.75)
    metrics.record_trade("SOL", "solana", 100.5, 105.2, "take_profit")
    summary = metrics.get_summary()
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from loguru import logger


@dataclass
class SignalRecord:
    """Record of a generated signal."""
    token: str
    source: str
    signal_type: str
    confidence: float
    timestamp: float


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    token: str
    chain: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    timestamp: float


@dataclass
class LatencyRecord:
    """Record of an API call latency."""
    service: str
    latency_ms: float
    success: bool
    timestamp: float


class MetricsCollector:
    """Centralized metrics collection for QuantAgent observability.

    Features:
    - Signal accuracy tracking per source
    - Trade PnL tracking with rolling window
    - API latency percentiles
    - System uptime and health
    - Rolling window support (configurable retention)
    """

    def __init__(
        self,
        window_size: int = 1000,
        latency_window: int = 500,
    ):
        self.window_size = window_size
        self.latency_window = latency_window
        self._lock = Lock()

        # Counters
        self._start_time = time.time()
        self._total_cycles = 0
        self._total_signals = 0
        self._total_trades = 0
        self._total_errors = 0

        # Rolling windows
        self._signals: deque[SignalRecord] = deque(maxlen=window_size)
        self._trades: deque[TradeRecord] = deque(maxlen=window_size)
        self._latencies: dict[str, deque[LatencyRecord]] = defaultdict(
            lambda: deque(maxlen=latency_window)
        )

        # Per-source signal accuracy
        self._signal_correct: dict[str, int] = defaultdict(int)
        self._signal_total: dict[str, int] = defaultdict(int)

        # Per-token trade stats
        self._token_pnl: dict[str, float] = defaultdict(float)
        self._token_trades: dict[str, int] = defaultdict(int)
        self._token_wins: dict[str, int] = defaultdict(int)

    def record_signal(
        self,
        token: str,
        source: str,
        signal_type: str,
        confidence: float,
    ) -> None:
        """Record a generated signal."""
        with self._lock:
            self._total_signals += 1
            self._signals.append(SignalRecord(
                token=token,
                source=source,
                signal_type=signal_type,
                confidence=confidence,
                timestamp=time.time(),
            ))

    def record_signal_outcome(
        self,
        source: str,
        correct: bool,
    ) -> None:
        """Record whether a signal prediction was correct."""
        with self._lock:
            self._signal_total[source] += 1
            if correct:
                self._signal_correct[source] += 1

    def record_trade(
        self,
        token: str,
        chain: str,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        """Record a completed trade."""
        pnl_pct = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        with self._lock:
            self._total_trades += 1
            self._trades.append(TradeRecord(
                token=token,
                chain=chain,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                exit_reason=exit_reason,
                timestamp=time.time(),
            ))
            self._token_pnl[token] += pnl_pct
            self._token_trades[token] += 1
            if pnl_pct > 0:
                self._token_wins[token] += 1

    def record_latency(
        self,
        service: str,
        latency_ms: float,
        success: bool = True,
    ) -> None:
        """Record an API call latency."""
        with self._lock:
            self._latencies[service].append(LatencyRecord(
                service=service,
                latency_ms=latency_ms,
                success=success,
                timestamp=time.time(),
            ))

    def record_cycle(self) -> None:
        """Record a completed monitoring cycle."""
        with self._lock:
            self._total_cycles += 1

    def record_error(self) -> None:
        """Record an error occurrence."""
        with self._lock:
            self._total_errors += 1

    def get_summary(self) -> dict:
        """Get a comprehensive metrics summary."""
        with self._lock:
            uptime = time.time() - self._start_time

            # Signal accuracy by source
            signal_accuracy = {}
            for source in self._signal_total:
                total = self._signal_total[source]
                correct = self._signal_correct[source]
                signal_accuracy[source] = {
                    "accuracy": correct / total if total > 0 else 0,
                    "total": total,
                    "correct": correct,
                }

            # Trade stats
            trade_stats = {}
            for token in self._token_trades:
                total = self._token_trades[token]
                wins = self._token_wins[token]
                pnl = self._token_pnl[token]
                trade_stats[token] = {
                    "total_trades": total,
                    "win_rate": wins / total if total > 0 else 0,
                    "total_pnl_pct": round(pnl, 2),
                    "avg_pnl_pct": round(pnl / total, 4) if total > 0 else 0,
                }

            # Latency stats
            latency_stats = {}
            for service, records in self._latencies.items():
                if not records:
                    continue
                latencies = [r.latency_ms for r in records]
                successes = sum(1 for r in records if r.success)
                latency_stats[service] = {
                    "p50": round(sorted(latencies)[len(latencies) // 2], 2),
                    "p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if len(latencies) >= 20 else None,
                    "p99": round(sorted(latencies)[int(len(latencies) * 0.99)], 2) if len(latencies) >= 100 else None,
                    "avg_ms": round(sum(latencies) / len(latencies), 2),
                    "success_rate": successes / len(records),
                    "total_calls": len(records),
                }

            # Overall trade performance
            all_trades = list(self._trades)
            overall_win_rate = 0
            overall_pnl = 0
            if all_trades:
                wins = sum(1 for t in all_trades if t.pnl_pct > 0)
                overall_win_rate = wins / len(all_trades)
                overall_pnl = sum(t.pnl_pct for t in all_trades)

            return {
                "uptime_seconds": round(uptime, 0),
                "uptime_human": f"{uptime / 3600:.1f}h",
                "total_cycles": self._total_cycles,
                "total_signals": self._total_signals,
                "total_trades": self._total_trades,
                "total_errors": self._total_errors,
                "error_rate": self._total_errors / max(self._total_cycles, 1),
                "overall_win_rate": round(overall_win_rate, 3),
                "overall_pnl_pct": round(overall_pnl, 2),
                "signal_accuracy": signal_accuracy,
                "trade_stats": trade_stats,
                "latency_stats": latency_stats,
                "signals_last_hour": sum(
                    1 for s in self._signals if time.time() - s.timestamp < 3600
                ),
                "trades_last_hour": sum(
                    1 for t in self._trades if time.time() - t.timestamp < 3600
                ),
            }


# ─── Global singleton ───

_instance: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _instance
    if _instance is None:
        _instance = MetricsCollector()
    return _instance
