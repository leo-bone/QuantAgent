"""Agent memory store - records decisions and outcomes for learning."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from quantagent.common.models import AgentMemory, CombinedSignal, StrategyAction


class AgentMemoryStore:
    """In-memory and file-based storage for agent decisions.

    Records every decision the agent makes, along with the signal that
    triggered it and (eventually) the outcome. This data is used for:
    1. Post-hoc analysis of strategy performance
    2. Training better decision models
    3. Debugging unexpected behavior
    """

    def __init__(self, persist_dir: Optional[str] = None, max_memory: int = 10000):
        self._decisions: list[AgentMemory] = []
        self.max_memory = max_memory
        self.persist_dir = Path(persist_dir) if persist_dir else None

    def record_decision(
        self,
        signal: CombinedSignal,
        action: StrategyAction,
        outcome: Optional[str] = None,
    ) -> str:
        """Record a decision and return the decision ID."""
        decision_id = f"D{len(self._decisions)+1:06d}"

        memory = AgentMemory(
            decision_id=decision_id,
            timestamp=datetime.now(timezone.utc),
            signal=signal,
            action_taken=action,
            outcome=outcome,
        )

        self._decisions.append(memory)

        # Trim if exceeding max
        if len(self._decisions) > self.max_memory:
            self._decisions = self._decisions[-self.max_memory:]

        logger.debug(f"Recorded decision {decision_id}: {action.action.value} {action.token}")
        return decision_id

    def update_outcome(self, decision_id: str, outcome: str, pnl_usd: float) -> None:
        """Update the outcome of a previous decision."""
        for d in self._decisions:
            if d.decision_id == decision_id:
                d.outcome = outcome
                d.pnl_usd = pnl_usd
                break

    def get_recent_decisions(self, limit: int = 50) -> list[AgentMemory]:
        """Get recent decisions."""
        return self._decisions[-limit:]

    def get_performance_stats(self) -> dict:
        """Calculate performance statistics from memory."""
        if not self._decisions:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_pnl": 0.0}

        with_outcomes = [d for d in self._decisions if d.outcome]
        wins = sum(1 for d in with_outcomes if d.outcome == "profit")
        losses = sum(1 for d in with_outcomes if d.outcome == "loss")
        pnls = [d.pnl_usd for d in with_outcomes if d.pnl_usd is not None]

        return {
            "total": len(with_outcomes),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(with_outcomes) if with_outcomes else 0.0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "total_pnl": sum(pnls) if pnls else 0.0,
        }

    def persist(self) -> None:
        """Persist decisions to disk."""
        if not self.persist_dir:
            return

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.persist_dir / f"memory_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"

        data = []
        for d in self._decisions:
            data.append({
                "decision_id": d.decision_id,
                "timestamp": d.timestamp.isoformat(),
                "signal_type": d.signal.signal_type.value,
                "token": d.signal.token,
                "chain": d.signal.chain.value,
                "confidence": d.signal.confidence,
                "action": d.action_taken.action.value,
                "outcome": d.outcome,
                "pnl_usd": d.pnl_usd,
            })

        filepath.write_text(json.dumps(data, indent=2))
        logger.info(f"Memory persisted to {filepath}")
