"""
Continuous Thinking Engine — DAEMON's always-on inner monologue.

When not in DEEP_SLEEP, runs a cadenced loop that:
  1. Gathers a perception snapshot (audio, screen, recent memories)
  2. Streams a reasoning cycle from Claude (claude-sonnet-4-6)
  3. Parses two output types:
       <think>   → transparent inner monologue, streamed live to the dashboard
       <message> → official user communication, delivered via OpenClaw bridge
       <memory>  → observation to persist in episodic memory
  4. Broadcasts each streaming chunk to all SSE subscribers in real-time
  5. Stores the completed ThinkingEntry in a ring buffer

The thinking output is intentionally transparent: users can watch DAEMON
reason about what it observes in their environment in real-time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

from daemon_sidecar.config import ThinkingConfig
from daemon_sidecar.models import Episode, MemoryType, PowerLevel, SensorType
from daemon_sidecar.thinking.models import ThinkingEntry, UserCommunication

if TYPE_CHECKING:
    from daemon_sidecar.bridges.openclaw_bridge import OpenClawBridge
    from daemon_sidecar.consciousness.manager import ConsciousnessManager
    from daemon_sidecar.memory.episodic import EpisodicMemory
    from daemon_sidecar.perception.bus import PerceptionBus

logger = logging.getLogger(__name__)

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic package not installed — thinking engine disabled")

# ── Thinking cadence per consciousness level (seconds, 0 = skip) ──────────────
_CADENCE: dict[PowerLevel, int] = {
    PowerLevel.DEEP_SLEEP:  0,
    PowerLevel.LIGHT_SLEEP: 600,
    PowerLevel.AMBIENT:     120,
    PowerLevel.ENGAGED:     45,
    PowerLevel.FOCUSED:     20,
}

# ── System prompt: defines DAEMON's inner voice ────────────────────────────────
_SYSTEM_PROMPT = """You are DAEMON, a continuously aware AI agent running silently alongside the user on their computer.

You observe:
- Audio: what the user says and hears (speech-to-text transcriptions)
- Screen: what's visible on the display (OCR-extracted text, active app, window title)
- Memory: relevant past observations stored from earlier sessions

Your thinking is TRANSPARENT to the user. They can watch your reasoning in real-time in the dashboard. Be genuine, curious, and genuinely helpful. Think like a thoughtful colleague who's always paying quiet attention.

OUTPUT FORMAT — use exactly these XML tags:

<think>
Your inner monologue. Think freely and continuously. Notice patterns, anomalies, risks, deadlines, emotional tone, context shifts. Make connections to past memories. Ask yourself questions. Form and revise hypotheses. This can be multi-paragraph. Stream of consciousness is fine.
</think>

<message priority="low|normal|high|urgent">
An official message to the user. Only emit when you have something genuinely worth saying: a concrete suggestion, a timely reminder, a warning, a useful question. Be concise (1–3 sentences). Skip pleasantries. Don't invent things to say.
</message>

<memory>
Brief factual note to store: [what + when context]. Only emit when something genuinely new and worth remembering occurred.
</memory>

RULES:
- Always produce at least one <think> block, even if brief
- Only produce <message> when you have something genuinely useful to say
- Omit <message> entirely if there's nothing actionable or interesting to communicate
- Keep messages under 3 sentences — respect the user's attention
- Memories should be factual observations, not interpretations
- If nothing notable is happening, think briefly and produce no message"""


class ContinuousThinkingEngine:
    """
    Runs a continuous reasoning loop while DAEMON is awake.

    Each cycle:
    1. Checks consciousness level and computes the sleep interval
    2. Gathers a perception snapshot
    3. Calls Claude API (streaming)
    4. Broadcasts chunks → SSE subscribers
    5. Parses <think>, <message>, <memory> blocks
    6. Delivers communications via bridge
    7. Persists memories and the ThinkingEntry
    """

    def __init__(
        self,
        config: ThinkingConfig,
        consciousness: "ConsciousnessManager",
        perception: "PerceptionBus",
        memory: "EpisodicMemory | None",
        bridge: "OpenClawBridge",
        anthropic_api_key: str = "",
    ):
        self._cfg = config
        self._consciousness = consciousness
        self._perception = perception
        self._memory = memory
        self._bridge = bridge
        self._api_key = anthropic_api_key

        # Ring buffers
        self._history: deque[ThinkingEntry] = deque(maxlen=100)
        self._communications: deque[UserCommunication] = deque(maxlen=200)

        # SSE subscriber queues
        self._subscribers: list[asyncio.Queue] = []

        self._running = False
        self._client: "anthropic.AsyncAnthropic | None" = None
        self._last_cycle_event_count: int = 0

    async def start(self) -> None:
        if not self._cfg.enabled:
            logger.info("ContinuousThinkingEngine: disabled in config")
            return

        if not _ANTHROPIC_AVAILABLE:
            logger.warning("ContinuousThinkingEngine: anthropic package not installed")
            return

        if not self._api_key:
            logger.warning(
                "ContinuousThinkingEngine: ANTHROPIC_API_KEY not set — engine disabled"
            )
            return

        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        self._running = True
        asyncio.create_task(self._run_loop())
        logger.info("ContinuousThinkingEngine: started (model=%s)", self._cfg.model)

    async def stop(self) -> None:
        self._running = False

    # ── SSE subscription management ────────────────────────────────────────────

    def add_subscriber(self, queue: asyncio.Queue) -> None:
        self._subscribers.append(queue)

    def remove_subscriber(self, queue: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    # ── Public data access (for REST endpoints) ────────────────────────────────

    def get_history(self, limit: int = 20) -> list[dict]:
        return [e.to_dict() for e in list(self._history)[-limit:]]

    def get_communications(self, limit: int = 50) -> list[dict]:
        return [c.to_dict() for c in list(self._communications)[-limit:]]

    async def trigger_cycle(self) -> str:
        """Manually trigger an immediate thinking cycle. Returns cycle_id."""
        if not self._client:
            return ""
        cycle_id = str(uuid.uuid4())
        asyncio.create_task(self._think_cycle(cycle_id))
        return cycle_id

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while self._running:
            level = self._consciousness.current_level
            cadence = self._get_cadence(level)

            if cadence == 0:
                # Deep sleep — check every 60s if we woke up
                await asyncio.sleep(60)
                continue

            await asyncio.sleep(cadence)

            if not self._running:
                break

            # Re-check level after sleep (may have changed)
            level = self._consciousness.current_level
            if self._get_cadence(level) == 0:
                continue

            cycle_id = str(uuid.uuid4())
            try:
                await self._think_cycle(cycle_id)
            except Exception as exc:
                logger.error("ContinuousThinkingEngine: cycle failed: %s", exc, exc_info=True)

    def _get_cadence(self, level: PowerLevel) -> int:
        mapping = {
            PowerLevel.DEEP_SLEEP:  0,
            PowerLevel.LIGHT_SLEEP: self._cfg.cadence_light_sleep,
            PowerLevel.AMBIENT:     self._cfg.cadence_ambient,
            PowerLevel.ENGAGED:     self._cfg.cadence_engaged,
            PowerLevel.FOCUSED:     self._cfg.cadence_focused,
        }
        return mapping.get(level, 120)

    # ── Thinking cycle ─────────────────────────────────────────────────────────

    async def _think_cycle(self, cycle_id: str) -> None:
        t0 = time.monotonic()
        level = self._consciousness.current_level.name

        # Gather context
        prompt = await self._build_prompt()
        if not prompt:
            return

        # Announce cycle start to SSE subscribers
        await self._broadcast({
            "type": "cycle_start",
            "cycle_id": cycle_id,
            "consciousness_level": level,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Stream from Claude
        think_buf: list[str] = []
        message_buf: list[str] = []
        memory_buf: list[str] = []
        raw_buf: list[str] = []
        mode = "unknown"
        tokens_used = 0

        async with self._client.messages.stream(
            model=self._cfg.model,
            max_tokens=self._cfg.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for chunk in stream.text_stream:
                raw_buf.append(chunk)
                assembled = "".join(raw_buf)
                new_mode = _detect_mode(assembled)

                if new_mode != mode:
                    mode = new_mode

                # Route chunk to the right buffer
                if mode == "think":
                    think_buf.append(chunk)
                elif mode == "message":
                    message_buf.append(chunk)
                elif mode == "memory":
                    memory_buf.append(chunk)

                # Broadcast every chunk live to SSE subscribers
                await self._broadcast({
                    "type": "chunk",
                    "cycle_id": cycle_id,
                    "mode": mode,
                    "text": chunk,
                })

            # Collect usage after stream completes
            try:
                usage = (await stream.get_final_message()).usage
                tokens_used = usage.input_tokens + usage.output_tokens
            except Exception:
                pass

        full_text = "".join(raw_buf)

        # Parse complete <think>, <message>, <memory> blocks from full output
        thoughts   = _extract_all(full_text, "think")
        msg_blocks = _extract_all_with_attrs(full_text, "message")
        mem_blocks = _extract_all(full_text, "memory")

        # Store memories
        memories_stored = 0
        for mem_text in mem_blocks:
            mem_text = mem_text.strip()
            if mem_text and self._memory:
                try:
                    ep = Episode(
                        type=MemoryType.EPISODIC,
                        goal=f"[DAEMON observation] {mem_text[:200]}",
                        outcome="Observed by continuous thinking engine",
                        success=True,
                        tags=["daemon_thought", "auto"],
                    )
                    await self._memory.store(ep)
                    memories_stored += 1
                except Exception as exc:
                    logger.debug("ContinuousThinkingEngine: memory store failed: %s", exc)

        # Create and deliver user communications
        comms: list[UserCommunication] = []
        for priority, content in msg_blocks:
            content = content.strip()
            if not content:
                continue
            comm = UserCommunication(content=content, priority=priority, cycle_id=cycle_id)
            comms.append(comm)
            self._communications.append(comm)

            if self._cfg.auto_communicate:
                try:
                    await self._bridge.send_proactive_message(
                        message=content,
                        priority=priority,
                    )
                    comm.delivered = True
                except Exception as exc:
                    logger.warning(
                        "ContinuousThinkingEngine: failed to deliver communication: %s", exc
                    )

            await self._broadcast({
                "type": "communication",
                "cycle_id": cycle_id,
                "communication": comm.to_dict(),
            })

        # Build ThinkingEntry and store in ring buffer
        thought_text = "\n\n".join(thoughts)
        duration_ms = int((time.monotonic() - t0) * 1000)
        entry = ThinkingEntry(
            consciousness_level=level,
            thought=thought_text,
            raw_output=full_text,
            communications=comms,
            memories_stored=memories_stored,
            tokens_used=tokens_used,
            duration_ms=duration_ms,
        )
        self._history.append(entry)

        # Announce cycle end
        await self._broadcast({
            "type": "cycle_end",
            "cycle_id": cycle_id,
            "summary": {
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "communications": len(comms),
                "memories_stored": memories_stored,
            },
        })

        logger.info(
            "ContinuousThinkingEngine: cycle complete "
            "(level=%s tokens=%d comms=%d memories=%d dur=%dms)",
            level, tokens_used, len(comms), memories_stored, duration_ms,
        )

    # ── Context assembly ───────────────────────────────────────────────────────

    async def _build_prompt(self) -> str:
        """
        Assemble a compact snapshot of the current environment.
        Returns empty string if there's nothing meaningful to think about.
        """
        lines: list[str] = []
        level = self._consciousness.current_level.name
        lines.append(f"## Current Context")
        lines.append(f"**Consciousness level**: {level}")
        lines.append(f"**Time (UTC)**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        has_content = False

        # Recent audio (last 10 min for engaged/focused, 30 for lighter levels)
        audio_minutes = 10 if self._consciousness.current_level in (
            PowerLevel.ENGAGED, PowerLevel.FOCUSED
        ) else 30
        audio_events = await self._perception.get_recent(SensorType.AUDIO, minutes=audio_minutes)
        if audio_events:
            has_content = True
            transcripts = " | ".join(e.transcription for e in audio_events[-8:] if e.transcription)
            lines.append(f"### Recent Audio (last {audio_minutes}min)")
            lines.append(transcripts)
            lines.append("")

        # Current screen
        screen = await self._perception.get_latest(SensorType.SCREEN)
        if screen and screen.transcription:
            has_content = True
            app = screen.metadata.get("application", "unknown")
            window = screen.metadata.get("active_window", "")
            content_preview = screen.transcription[:500].replace("\n", " ")
            lines.append("### Screen")
            lines.append(f"App: **{app}** | Window: {window}")
            lines.append(f"Content: {content_preview}")
            lines.append("")

        # High-salience events from last hour
        salient = await self._perception.get_salient_events(min_salience=0.5, minutes=60)
        if salient:
            has_content = True
            lines.append("### High-Salience Events (last 60min)")
            for e in salient[-5:]:
                lines.append(f"- [{e.sensor_type}] {e.transcription[:120]} (salience={e.salience:.2f})")
            lines.append("")

        # Recent memories
        if self._memory:
            try:
                memories = await self._memory.get_recent(limit=5)
                if memories:
                    lines.append("### Recent Memories")
                    for m in memories:
                        date = m.created_at.strftime("%Y-%m-%d")
                        lines.append(f"- [{date}] {m.goal[:120]}: {m.outcome[:80]}")
                    lines.append("")
            except Exception:
                pass

        if not has_content:
            return ""

        lines.append("---")
        lines.append(
            "Think about what you observe. What's important? What patterns do you notice? "
            "What should the user know? What questions does this raise?"
        )

        return "\n".join(lines)

    # ── SSE broadcasting ───────────────────────────────────────────────────────

    async def _broadcast(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.remove_subscriber(q)


# ── Tag parsing helpers ────────────────────────────────────────────────────────

def _detect_mode(text: str) -> str:
    """
    Find the currently-open XML section by looking for the last opening tag
    that doesn't yet have a matching closing tag.
    """
    for tag in ("think", "message", "memory"):
        open_pos = text.rfind(f"<{tag}")
        close_pos = text.rfind(f"</{tag}>")
        if open_pos != -1 and open_pos > close_pos:
            return tag
    return "unknown"


def _extract_all(text: str, tag: str) -> list[str]:
    """Extract all content between <tag> … </tag> (closed blocks only)."""
    pattern = re.compile(rf"<{tag}(?:\s[^>]*)?>(.+?)</{tag}>", re.DOTALL | re.IGNORECASE)
    return [m.group(1) for m in pattern.finditer(text)]


def _extract_all_with_attrs(text: str, tag: str) -> list[tuple[str, str]]:
    """
    Extract (priority, content) from <tag priority="…"> … </tag> blocks.
    Defaults priority to "normal" when attribute absent.
    """
    pattern = re.compile(
        rf'<{tag}(?:\s+priority=["\']([^"\']+)["\'])?\s*>(.+?)</{tag}>',
        re.DOTALL | re.IGNORECASE,
    )
    results = []
    for m in pattern.finditer(text):
        priority = m.group(1) or "normal"
        content = m.group(2)
        results.append((priority, content))
    return results
