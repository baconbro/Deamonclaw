"""
DAEMON Sidecar — runs alongside OpenClaw, provides:

1. Perception pipeline      (audio / video / screen sensors)          Phase 2
2. Structured memory        (PostgreSQL + pgvector, vector recall)     Phase 2
3. Safety layer             (permission tiers, side-effect ledger)     Phase 1
4. Consciousness mgr        (sleep/wake states, power levels)          Phase 1
5. Proactive engine         (interrupt budget, quiet hours)            Phase 1
6. Context builder          (daemon_context XML injection)             Phase 3
7. Bridge management        (queue, reconnect, status)                 Phase 3
8. Continuous thinking      (Claude-powered inner monologue + comms)   Phase 5
9. REST API                 (called by OpenClaw skills)                Phase 1
10. Web dashboard           (served at /)                              Phase 4

Communication with OpenClaw:
  • REST API           — OpenClaw skills → sidecar (Integration Point 1)
  • WebSocket client   — sidecar → OpenClaw gateway proactive msgs (Point 2)
  • WebSocket listener — sidecar monitors all OpenClaw events (Point 3)
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from daemon_sidecar.bridges.context_builder import ContextBuilder
from daemon_sidecar.bridges.event_listener import CostTracker, OpenClawEventListener
from daemon_sidecar.bridges.openclaw_bridge import OpenClawBridge
from daemon_sidecar.config import load_config
from daemon_sidecar.consciousness.manager import ConsciousnessManager
from daemon_sidecar.memory.embeddings import EmbeddingSalienceScorer
from daemon_sidecar.memory.episodic import EpisodicMemory
from daemon_sidecar.models import (
    Episode,
    LedgerEntry,
    LogActionRequest,
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
from daemon_sidecar.thinking.engine import ContinuousThinkingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_HERE = pathlib.Path(__file__).parent
_STATIC = _HERE / "static"
_TEMPLATES_DIR = _HERE / "templates"
_STATIC.mkdir(exist_ok=True)
_TEMPLATES_DIR.mkdir(exist_ok=True)


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

    # ── Embedding scorer (shared by memory recall + context builder) ───────────
    app.state.scorer = EmbeddingSalienceScorer()
    await app.state.scorer.initialize()

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

    # ── Event listener (OpenClaw → sidecar, Integration Point 3) ─────────────
    if app.state.memory and app.state.ledger:
        app.state.event_listener = OpenClawEventListener(
            gateway_url=config.openclaw_gateway_url,
            ledger=app.state.ledger,
            memory=app.state.memory,
            costs=app.state.costs,
            consciousness=app.state.consciousness,
        )
        asyncio.create_task(app.state.event_listener.listen())

    # ── Perception bus (Phase 2: real sensors) ────────────────────────────────
    app.state.perception = PerceptionBus(
        config=config.perception,
        consciousness=app.state.consciousness,
        proactive=app.state.proactive,
        bridge=app.state.bridge,
    )
    await app.state.perception.start()

    # ── Context builder (Phase 3) ─────────────────────────────────────────────
    app.state.context_builder = ContextBuilder(
        perception=app.state.perception,
        memory=app.state.memory,
        consciousness=app.state.consciousness,
        permissions=app.state.permissions,
        scorer=app.state.scorer,
    )

    # ── Continuous thinking engine (Phase 5) ──────────────────────────────────
    app.state.thinking = ContinuousThinkingEngine(
        config=config.thinking,
        consciousness=app.state.consciousness,
        perception=app.state.perception,
        memory=app.state.memory,
        bridge=app.state.bridge,
        anthropic_api_key=config.anthropic_api_key,
    )
    await app.state.thinking.start()

    logger.info("DAEMON sidecar ready on port %d", config.api_port)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await app.state.thinking.stop()
    await app.state.perception.stop()
    logger.info("DAEMON sidecar stopped")


app = FastAPI(
    title="DAEMON Sidecar",
    description="Perception, memory, safety, and continuous thinking for OpenClaw",
    version="0.3.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard_ui(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Memory API ─────────────────────────────────────────────────────────────────

@app.post("/api/memory/store")
async def store_memory(request: StoreMemoryRequest) -> dict[str, Any]:
    memory = _require_memory()
    episode = Episode.from_request(request)
    embedding = await app.state.scorer.embed(f"{episode.goal} {episode.outcome}")
    if embedding:
        await memory.store_with_embedding(episode, embedding)
    else:
        await memory.store(episode)
    return {"stored": True, "id": episode.id}


@app.post("/api/memory/recall")
async def recall_memory(request: RecallRequest) -> dict[str, Any]:
    memory = _require_memory()
    embedding = await app.state.scorer.embed(request.query)
    if embedding:
        results = await memory.recall_semantic(embedding, limit=request.limit)
        if results:
            return {"memories": [r.to_summary() for r in results], "method": "semantic"}
    results = await memory.recall(query=request.query, limit=request.limit)
    return {"memories": [r.to_summary() for r in results], "method": "text"}


@app.get("/api/memory/recent")
async def recent_memories(limit: int = 10) -> dict[str, Any]:
    memory = _require_memory()
    results = await memory.get_recent(limit)
    return {"memories": [r.to_summary() for r in results]}


# ── Safety API ─────────────────────────────────────────────────────────────────

@app.post("/api/safety/check")
async def check_permission(request: PermissionCheckRequest) -> dict[str, Any]:
    result = await app.state.permissions.check(request)
    return result.model_dump()


@app.post("/api/safety/log")
async def log_action(request: LogActionRequest) -> dict[str, Any]:
    ledger = _require_ledger()
    await ledger.record(LedgerEntry.from_request(request))
    return {"logged": True}


# ── Perception API ─────────────────────────────────────────────────────────────

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


# ── Bridge API ─────────────────────────────────────────────────────────────────

class InjectContextRequest(BaseModel):
    user_query: str = ""
    audio_minutes: int = 5
    memory_limit: int = 3


@app.post("/api/bridge/inject-context")
async def inject_context(request: InjectContextRequest) -> dict[str, Any]:
    context_xml = await app.state.context_builder.build(
        user_query=request.user_query,
        audio_minutes=request.audio_minutes,
        memory_limit=request.memory_limit,
    )
    if context_xml:
        await app.state.bridge.inject_context(context_xml)
    return {"injected": bool(context_xml), "length": len(context_xml)}


@app.get("/api/bridge/status")
async def bridge_status() -> dict[str, Any]:
    return app.state.bridge.status()


# ── Thinking API (Phase 5) ─────────────────────────────────────────────────────

@app.get("/api/thinking/stream")
async def thinking_stream(request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream of live thinking output.

    Each event is a JSON object with type:
      cycle_start   — new thinking cycle beginning
      chunk         — streaming text chunk (mode: think|message|memory|unknown)
      cycle_end     — cycle complete with summary stats
      communication — a user communication was generated
      keepalive     — connection keepalive (every 15s of silence)
    """
    engine: ContinuousThinkingEngine = app.state.thinking
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    engine.add_subscriber(q)

    async def generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type":"keepalive"}\n\n'
        finally:
            engine.remove_subscriber(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/thinking/history")
async def thinking_history(limit: int = 20) -> dict[str, Any]:
    """Return the last N completed thinking cycles."""
    return {"cycles": app.state.thinking.get_history(limit)}


@app.get("/api/thinking/communications")
async def thinking_communications(limit: int = 50) -> dict[str, Any]:
    """Return the last N user communications generated by the thinking engine."""
    return {"communications": app.state.thinking.get_communications(limit)}


@app.post("/api/thinking/trigger")
async def trigger_thinking() -> dict[str, Any]:
    """Manually trigger an immediate thinking cycle (useful for testing)."""
    cycle_id = await app.state.thinking.trigger_cycle()
    if not cycle_id:
        raise HTTPException(503, "Thinking engine not available")
    return {"cycle_id": cycle_id, "triggered": True}


# ── Dashboard API ──────────────────────────────────────────────────────────────

@app.get("/api/dashboard/overview")
async def dashboard_overview() -> dict[str, Any]:
    c = app.state.consciousness
    memory_count = await app.state.memory.count() if app.state.memory else 0
    ledger_today = await app.state.ledger.count_today() if app.state.ledger else 0
    pending = await app.state.permissions.pending_count()
    salient = await app.state.perception.get_salient_events(min_salience=0.5, minutes=60)
    recent_comms = app.state.thinking.get_communications(limit=3)
    return {
        "consciousness": c.current_level.name,
        "active_sensors": c.active_sensors(),
        "last_significant_event": c.last_significant_event,
        "memory_count": memory_count,
        "ledger_entries_today": ledger_today,
        "costs_today": round(app.state.costs.today_total(), 4),
        "costs_this_hour": round(app.state.costs.current_hour_total(), 4),
        "pending_approvals": pending,
        "salient_events_last_hour": len(salient),
        "bridge": app.state.bridge.status(),
        "recent_communications": recent_comms,
    }


@app.get("/api/dashboard/ledger")
async def dashboard_ledger(limit: int = 20) -> dict[str, Any]:
    ledger = _require_ledger()
    entries = await ledger.get_recent(limit)
    for e in entries:
        if hasattr(e.get("timestamp"), "isoformat"):
            e["timestamp"] = e["timestamp"].isoformat()
    return {"entries": entries}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    cfg = load_config("daemon.yaml")
    uvicorn.run("daemon_sidecar.main:app", host="0.0.0.0", port=cfg.api_port, reload=False)
