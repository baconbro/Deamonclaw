"""Side-effect audit ledger backed by PostgreSQL."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from daemon_sidecar.models import LedgerEntry, SideEffectType

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ledger (
    id               TEXT PRIMARY KEY,
    tool_name        TEXT NOT NULL,
    tool_input       JSONB NOT NULL DEFAULT '{}',
    tool_output      TEXT NOT NULL DEFAULT '',
    permission_tier  INTEGER NOT NULL DEFAULT 0,
    side_effect_type TEXT NOT NULL DEFAULT 'unknown',
    reversible       BOOLEAN NOT NULL DEFAULT TRUE,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class SideEffectLedger:
    """Append-only ledger of every external action the agent takes."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls, db_url: str) -> "SideEffectLedger":
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        instance = cls(pool)
        await instance._migrate()
        return instance

    async def _migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLE)

    async def record(self, entry: LedgerEntry) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ledger
                    (id, tool_name, tool_input, tool_output, permission_tier,
                     side_effect_type, reversible, timestamp)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
                """,
                entry.id,
                entry.tool_name,
                json.dumps(entry.tool_input),
                entry.tool_output,
                entry.permission_tier,
                entry.side_effect_type.value,
                entry.reversible,
                entry.timestamp,
            )
        logger.debug("Ledger: recorded %s (%s)", entry.tool_name, entry.side_effect_type)

    async def count_today(self) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM ledger WHERE timestamp >= NOW() - INTERVAL '24 hours'"
            )

    async def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ledger ORDER BY timestamp DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]
