"""
DAEMON Sidecar — runs alongside OpenClaw, provides:

1. Perception pipeline  (audio / video / screen sensors)
2. Structured memory    (PostgreSQL + pgvector)
3. Safety layer         (permission tiers, side-effect ledger)
4. Consciousness mgr    (sleep/wake states, power levels)
5. Proactive engine     (interrupt budget, quiet hours)
6. REST API             (called by OpenClaw skills)

Communication with OpenClaw:
  • REST API           — OpenClaw skills → sidecar (Integration Point 1)
  • WebSocket client   — sidecar → OpenClaw gateway for proactive msgs (Point 2)
  • WebSocket listener — sidecar monitors all OpenClaw events (Point 3)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from daemon_sidecar.bridges.event_listener import CostTracker, OpenClawEventListener
from daemon_sidecar.bridges.openclaw_bridge import OpenClawBridge
from daemon_sidecar.config import load_config
from daemon_sidecar.consciousness.manager import ConsciousnessManager
from daemon_sidecar.memory.episodic import EpisodicMemory
from daemon_sidecar.models import (
    Episode,
    LogActionRequest,
    LedgerEntry,
    PermissionCheckRequest,
    PowerLevel,
    RecallRequest,
    SearchRequest,
    SensorType,
    StoreMemoryRequest,
)
from daemon_sidecar.perception.bus import PerceptionBus
from daemon_sidecar.proactive.engine import ProactiveEngine
from daemon_sidecar.safety.ledger import SideEffectLedger
from daemon_sidecar.safety.permissions import PermissionGate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config("daemon.yaml")
    logger.info("DAEMON sidecar starting (api_port=%d)", config.api_port)

    # ── Core memory / ledger ──────────────────────────────────────────────────
    try:
        app.state.memory = await EpisodicMemory.connect(config.memory_db_url)
        app.state.ledger = await SideEffectLedger.connect(config.memory_db_url)
        logger.info("Database connected: %s", config.memory_db_url)
    except Exception as exc:
        logger.error("Database connection failed: %s — running without persistence", exc)
        app.state.memory = None
        app.state.ledger = None

    # ── Safety ────────────────────────────────────────────────────────────────
    app.state.permissions = PermissionGate(config.safety)

    # ── Cost tracker (in-memory) ──────────────────────────────────────────────
    app.state.costs = CostTracker()

    # ── Consciousness ─────────────────────────────────────────────────────────
    app.state.consciousness = ConsciousnessManager(config.consciousness)

    # ── OpenClaw bridge (sidecar → OpenClaw) ──────────────────────────────────
    app.state.bridge = OpenClawBridge(config.openclaw_gateway_url)
    await app.state.bridge.connect()

    # ── Proactive engine ──────────────────────────────────────────────────────
    app.state.proactive = ProactiveEngine(config.proactive)
    app.state.proactive.set_bridge(app.state.bridge)

    # ── Event listener (OpenClaw → sidecar) ───────────────────────────────────
    if app.state.memory and app.state.ledger:
        app.state.event_listener = OpenClawEventListener(
            gateway_url=config.openclaw_gateway_url,
            ledger=app.state.ledger,
            memory=app.state.memory,
            costs=app.state.costs,
            consciousness=app.state.consciousness,
        )
        asyncio.create_task(app.state.event_listener.listen())

    # ── Perception bus ────────────────────────────────────────────────────────
    app.state.perception = PerceptionBus(
        config=config.perception,
        consciousness=app.state.consciousness,
        proactive=app.state.proactive,
        bridge=app.state.bridge,
    )
    await app.state.perception.start()

    logger.info("DAEMON sidecar ready on port %d", config.api_port)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await app.state.perception.stop()
    logger.info("DAEMON sidecar stopped")


app = FastAPI(
    title="DAEMON Sidecar",
    description="Perception, memory, and safety layer for OpenClaw",
    version="0.1.0",
    lifespan=lifespan,
)


def _require_memory() -> EpisodicMemory:
    mem = app.state.memory
    if mem is None:
        raise HTTPException(503, "Memory store unavailable (database not connected)")
    return mem


def _require_ledger() -> SideEffectLedger:
    ledger = app.state.ledger
    if ledger is None:
        raise HTTPException(503, "Ledger unavailable (database not connected)")
    return ledger


# ── Memory API (Integration Point 1: called by daemon-memory skill) ───────────

@app.post("/api/memory/store")
async def store_memory(request: StoreMemoryRequest) -> dict[str, Any]:
    memory = _require_memory()
    episode = Episode.from_request(request)
    await memory.store(episode)
    return {"stored": True, "id": episode.id}


@app.post("/api/memory/recall")
async def recall_memory(request: RecallRequest) -> dict[str, Any]:
    memory = _require_memory()
    results = await memory.recall(query=request.query, limit=request.limit)
    return {"memories": [r.to_summary() for r in results]}


@app.get("/api/memory/recent")
async def recent_memories(limit: int = 10) -> dict[str, Any]:
    memory = _require_memory()
    results = await memory.get_recent(limit)
    return {"memories": [r.to_summary() for r in results]}


# ── Safety API (Integration Point 1: called by daemon-safety skill) ───────────

@app.post("/api/safety/check")
async def check_permission(request: PermissionCheckRequest) -> dict[str, Any]:
    result = await app.state.permissions.check(request)
    return result.model_dump()


@app.post("/api/safety/log")
async def log_action(request: LogActionRequest) -> dict[str, Any]:
    ledger = _require_ledger()
    await ledger.record(LedgerEntry.from_request(request))
    return {"logged": True}


# ── Perception API (Integration Point 1: called by daemon-perception skill) ───

@app.get("/api/perception/audio/recent")
async def recent_audio(minutes: int = 30) -> dict[str, Any]:
    events = await app.state.perception.get_recent(SensorType.AUDIO, minutes=minutes)
    return {"transcriptions": [e.transcription for e in events]}


@app.get("/api/perception/screen/current")
async def current_screen() -> dict[str, Any]:
    latest = await app.state.perception.get_latest(SensorType.SCREEN)
    return {
        "text": latest.transcription if latest else None,
        "app": latest.metadata.get("application") if latest else None,
        "window": latest.metadata.get("active_window") if latest else None,
    }


@app.get("/api/perception/events")
async def perception_events(
    min_salience: float = 0.5,
    minutes: int = 60,
) -> dict[str, Any]:
    events = await app.state.perception.get_salient_events(
        min_salience=min_salience,
        minutes=minutes,
    )
    return {"events": [e.to_summary() for e in events]}


@app.post("/api/perception/search")
async def search_perceptions(request: SearchRequest) -> dict[str, Any]:
    results = await app.state.perception.search(query=request.query, limit=request.limit)
    return {"results": [r.to_summary() for r in results]}


# ── Consciousness API ──────────────────────────────────────────────────────────

@app.get("/api/consciousness/status")
async def consciousness_status() -> dict[str, Any]:
    c = app.state.consciousness
    return {
        "power_level": c.current_level.name,
        "active_sensors": c.active_sensors(),
        "last_significant_event": c.last_significant_event,
        "cost_this_hour": app.state.costs.current_hour_total(),
    }


@app.post("/api/consciousness/wake")
async def wake(level: str = "engaged") -> dict[str, Any]:
    try:
        power_level = PowerLevel[level.upper()]
    except KeyError:
        raise HTTPException(400, f"Unknown power level: {level}")
    await app.state.consciousness.force_wake(power_level)
    return {"level": app.state.consciousness.current_level.name}


@app.post("/api/consciousness/sleep")
async def sleep() -> dict[str, Any]:
    await app.state.consciousness.force_sleep()
    return {"level": "DEEP_SLEEP"}


# ── Dashboard API (for future web UI) ─────────────────────────────────────────

@app.get("/api/dashboard/overview")
async def dashboard() -> dict[str, Any]:
    c = app.state.consciousness
    memory_count = await app.state.memory.count() if app.state.memory else 0
    ledger_today = await app.state.ledger.count_today() if app.state.ledger else 0
    pending = await app.state.permissions.pending_count()
    return {
        "consciousness": c.current_level.name,
        "memory_count": memory_count,
        "ledger_entries_today": ledger_today,
        "costs_today": app.state.costs.today_total(),
        "active_sensors": c.active_sensors(),
        "pending_approvals": pending,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    cfg = load_config("daemon.yaml")
    uvicorn.run("daemon_sidecar.main:app", host="0.0.0.0", port=cfg.api_port, reload=False)
