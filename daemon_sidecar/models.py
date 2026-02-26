"""Shared Pydantic models for DAEMON sidecar API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class PowerLevel(str, Enum):
    DEEP_SLEEP = "deep_sleep"
    LIGHT_SLEEP = "light_sleep"
    AMBIENT = "ambient"
    ENGAGED = "engaged"
    FOCUSED = "focused"


class SensorType(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    SCREEN = "screen"


class SideEffectType(str, Enum):
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    FILE_DELETE = "file:delete"
    HTTP_GET = "http:get"
    HTTP_POST = "http:post"
    SHELL_RUN = "shell:run"
    EMAIL_SEND = "email:send"
    MESSAGE_SEND = "message:send"
    CALENDAR_CREATE = "calendar:create"
    PURCHASE_MAKE = "purchase:make"
    ACCOUNT_MODIFY = "account:modify"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    UNKNOWN = "unknown"


# ── Memory models ──────────────────────────────────────────────────────────────

class Episode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MemoryType = MemoryType.EPISODIC
    goal: str
    outcome: str
    success: bool = True
    tools_used: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    embedding: list[float] | None = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "goal": self.goal,
            "outcome": self.outcome,
            "success": self.success,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_request(cls, req: "StoreMemoryRequest") -> "Episode":
        return cls(
            type=req.type,
            goal=req.goal,
            outcome=req.outcome,
            success=req.success,
            tools_used=req.tools_used,
            tags=req.tags,
        )


# ── Safety / Ledger models ─────────────────────────────────────────────────────

class LedgerEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_output: str = ""
    permission_tier: int = 0
    side_effect_type: SideEffectType = SideEffectType.UNKNOWN
    reversible: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def from_request(cls, req: "LogActionRequest") -> "LedgerEntry":
        return cls(
            tool_name=req.tool,
            tool_input={"action": req.action},
            tool_output=req.result,
            reversible=req.reversible,
        )


class PermissionResult(BaseModel):
    approved: bool
    tier: int
    reason: str = ""

    def model_dump(self, **kwargs) -> dict[str, Any]:  # type: ignore[override]
        d = super().model_dump(**kwargs)
        if not d["reason"]:
            del d["reason"]
        return d


# ── Perception models ──────────────────────────────────────────────────────────

class PerceptionEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sensor_type: SensorType
    transcription: str = ""
    salience: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sensor_type": self.sensor_type,
            "transcription": self.transcription,
            "salience": self.salience,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


# ── API request / response models ─────────────────────────────────────────────

class StoreMemoryRequest(BaseModel):
    type: MemoryType = MemoryType.EPISODIC
    goal: str
    outcome: str
    success: bool = True
    tools_used: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RecallRequest(BaseModel):
    query: str
    limit: int = 5


class PermissionCheckRequest(BaseModel):
    action: str
    tool: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)


class LogActionRequest(BaseModel):
    action: str
    tool: str
    result: str = ""
    reversible: bool = True


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
