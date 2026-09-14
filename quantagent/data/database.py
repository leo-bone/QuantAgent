"""Persistence layer (stub).

``Database`` is constructed as ``Database(db_url)`` and later awaited via
``await database.init()`` (or similar) by ``quant_agent.py``. The real
implementation should wrap an async SQLAlchemy engine + sessionmakers.

It is intentionally forgiving: ``init``/``close`` are no-ops here so the agent
can run in-memory if the DB backend is unavailable.
"""

from __future__ import annotations

from typing import Any


class Database:
    """Minimal stub for the persistence layer."""

    def __init__(self, url: str = "") -> None:
        self.url = url

    async def init(self) -> None:
        """Connect / create schema. No-op stub."""
        # Real implementation: create_async_engine(self.url), run migrations.
        return None

    async def close(self) -> None:
        """Dispose connections. No-op stub."""
        return None
