"""Data models for the continuous thinking engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal


class ThinkingEntry:
    """
    One completed thinking cycle.

    Holds both the raw inner-monologue text (from <think> sections) and
    the user-facing communications (from <message> sections) generated
    during that cycle.
    """

    __slots__ = (
        "id", "timestamp", "consciousness_level",
        "thought", "raw_output",
        "communications", "memories_stored",
        "tokens_used", "duration_ms",
    )

    def __init__(
        self,
        consciousness_level: str,
        thought: str = "",
        raw_output: str = "",
        communications: list["UserCommunication"] | None = None,
        memories_stored: int = 0,
        tokens_used: int = 0,
        duration_ms: int = 0,
    ):
        self.id = str(uuid.uuid4())
        self.timestamp = datetime.utcnow()
        self.consciousness_level = consciousness_level
        self.thought = thought
        self.raw_output = raw_output
        self.communications = communications or []
        self.memories_stored = memories_stored
        self.tokens_used = tokens_used
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "consciousness_level": self.consciousness_level,
            "thought": self.thought,
            "communications": [c.to_dict() for c in self.communications],
            "memories_stored": self.memories_stored,
            "tokens_used": self.tokens_used,
            "duration_ms": self.duration_ms,
        }


class UserCommunication:
    """
    An official message from DAEMON to the user.

    Generated from <message> blocks in the thinking output.
    """

    VALID_PRIORITIES = {"low", "normal", "high", "urgent"}

    __slots__ = ("id", "timestamp", "content", "priority", "delivered", "cycle_id")

    def __init__(
        self,
        content: str,
        priority: str = "normal",
        cycle_id: str = "",
    ):
        self.id = str(uuid.uuid4())
        self.timestamp = datetime.utcnow()
        self.content = content.strip()
        self.priority = priority if priority in self.VALID_PRIORITIES else "normal"
        self.delivered = False
        self.cycle_id = cycle_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "priority": self.priority,
            "delivered": self.delivered,
            "cycle_id": self.cycle_id,
        }


# SSE event types emitted by the thinking engine
ThinkingEventType = Literal[
    "cycle_start",   # new thinking cycle beginning
    "chunk",         # streaming text chunk (mode = think|message|memory|unknown)
    "cycle_end",     # cycle complete with summary
    "communication", # a user communication was generated
    "keepalive",     # SSE keepalive (no data)
]
