"""Episodic memory backed by PostgreSQL + pgvector."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from daemon_sidecar.models import Episode, MemoryType

logger = logging.getLogger(__name__)

CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'episodic',
    goal        TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    success     BOOLEAN NOT NULL DEFAULT TRUE,
    tools_used  JSONB NOT NULL DEFAULT '[]',
    tags        JSONB NOT NULL DEFAULT '[]',
    embedding   vector(1536),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS episodes_embedding_idx
ON episodes USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
"""

CREATE_INTERACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS interactions (
    id         TEXT PRIMARY KEY,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    channel    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class EpisodicMemory:
    """Async episodic memory store backed by PostgreSQL + pgvector."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls, db_url: str) -> "EpisodicMemory":
        pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
        instance = cls(pool)
        await instance._migrate()
        return instance

    async def _migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_EXTENSION)
            await conn.execute(CREATE_TABLE)
            await conn.execute(CREATE_INTERACTIONS_TABLE)
            try:
                await conn.execute(CREATE_INDEX)
            except Exception:
                pass  # Index may already exist or pgvector version differences

    async def store(self, episode: Episode) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO episodes (id, type, goal, outcome, success, tools_used, tags, embedding, created_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9)
                ON CONFLICT (id) DO NOTHING
                """,
                episode.id,
                episode.type.value,
                episode.goal,
                episode.outcome,
                episode.success,
                json.dumps(episode.tools_used),
                json.dumps(episode.tags),
                episode.embedding,
                episode.created_at,
            )
        logger.debug("Stored episode %s", episode.id)

    async def recall(self, query: str, limit: int = 5) -> list[Episode]:
        """Semantic recall; falls back to text search when no embedding available."""
        async with self._pool.acquire() as conn:
            # Text-based fallback search (tsquery over goal + outcome)
            rows = await conn.fetch(
                """
                SELECT id, type, goal, outcome, success, tools_used, tags, created_at
                FROM episodes
                WHERE goal ILIKE $1 OR outcome ILIKE $1 OR tags::text ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                f"%{query}%",
                limit,
            )
        return [self._row_to_episode(r) for r in rows]

    async def get_recent(self, limit: int = 10) -> list[Episode]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, type, goal, outcome, success, tools_used, tags, created_at
                FROM episodes
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [self._row_to_episode(r) for r in rows]

    async def count(self) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM episodes")

    async def record_interaction(
        self,
        role: str,
        content: str,
        channel: str | None = None,
    ) -> None:
        import uuid as _uuid
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interactions (id, role, content, channel)
                VALUES ($1, $2, $3, $4)
                """,
                str(_uuid.uuid4()),
                role,
                content,
                channel,
            )

    @staticmethod
    def _row_to_episode(row: Any) -> Episode:
        return Episode(
            id=row["id"],
            type=MemoryType(row["type"]),
            goal=row["goal"],
            outcome=row["outcome"],
            success=row["success"],
            tools_used=json.loads(row["tools_used"]) if row["tools_used"] else [],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=row["created_at"],
        )
