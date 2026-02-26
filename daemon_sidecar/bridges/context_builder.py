"""
Context builder — assembles <daemon_context> XML for injection into OpenClaw LLM calls.

The injected context block makes the OpenClaw agent aware of:
  - What the user said recently (audio transcriptions)
  - What's currently on screen
  - Relevant past memories (text + optional vector recall)
  - Current consciousness level
  - Any pending approvals

The agent receives this as part of its system prompt / pre-turn context
(injected via the OpenClaw bridge's inject_context() method).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon_sidecar.consciousness.manager import ConsciousnessManager
    from daemon_sidecar.memory.embeddings import EmbeddingSalienceScorer
    from daemon_sidecar.memory.episodic import EpisodicMemory
    from daemon_sidecar.perception.bus import PerceptionBus
    from daemon_sidecar.safety.permissions import PermissionGate

from daemon_sidecar.models import SensorType

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Assembles all available contextual signals into a structured XML block
    that can be injected into OpenClaw's conversation context.

    Usage:
        builder = ContextBuilder(perception, memory, consciousness, permissions, scorer)
        xml = await builder.build(user_query="what should I focus on today?")
        await bridge.inject_context(xml)
    """

    def __init__(
        self,
        perception: "PerceptionBus",
        memory: "EpisodicMemory | None",
        consciousness: "ConsciousnessManager",
        permissions: "PermissionGate",
        scorer: "EmbeddingSalienceScorer | None" = None,
    ):
        self._perception = perception
        self._memory = memory
        self._consciousness = consciousness
        self._permissions = permissions
        self._scorer = scorer

    async def build(
        self,
        user_query: str = "",
        audio_minutes: int = 5,
        memory_limit: int = 3,
        include_screen: bool = True,
    ) -> str:
        """
        Build the full <daemon_context> XML block.

        Returns an empty string if there is nothing useful to inject.
        """
        sections: list[str] = []

        # ── Recent audio ────────────────────────────────────────────────────────
        audio_events = await self._perception.get_recent(
            SensorType.AUDIO, minutes=audio_minutes
        )
        if audio_events:
            transcripts = " | ".join(
                e.transcription for e in audio_events[-5:] if e.transcription
            )
            if transcripts:
                sections.append(f"<audio_recent>{_escape(transcripts)}</audio_recent>")

        # ── Current screen ──────────────────────────────────────────────────────
        if include_screen:
            screen = await self._perception.get_latest(SensorType.SCREEN)
            if screen and screen.transcription:
                app = screen.metadata.get("application", "")
                window = screen.metadata.get("active_window", "")
                content = screen.transcription[:600]
                parts = []
                if app:
                    parts.append(f"App: {app}")
                if window:
                    parts.append(f"Window: {window}")
                parts.append(f"Content: {content}")
                sections.append(f"<screen_context>{_escape(' | '.join(parts))}</screen_context>")

        # ── Relevant memories ───────────────────────────────────────────────────
        if self._memory and user_query:
            memories = []
            # Try vector recall first
            if self._scorer:
                try:
                    embedding = await self._scorer.embed(user_query)
                    if embedding:
                        memories = await self._memory.recall_semantic(
                            embedding, limit=memory_limit
                        )
                except Exception as exc:
                    logger.debug("Context builder: vector recall failed (%s), falling back", exc)

            # Fall back to text search
            if not memories:
                try:
                    memories = await self._memory.recall(user_query, limit=memory_limit)
                except Exception as exc:
                    logger.debug("Context builder: text recall failed: %s", exc)

            if memories:
                mem_items = [
                    f"[{m.created_at.strftime('%Y-%m-%d')}] {_escape(m.goal)}: {_escape(m.outcome)}"
                    for m in memories
                ]
                sections.append(
                    f"<relevant_memories>{' | '.join(mem_items)}</relevant_memories>"
                )

        # ── Consciousness ───────────────────────────────────────────────────────
        level = self._consciousness.current_level.name
        active_sensors = ", ".join(self._consciousness.active_sensors()) or "none"
        sections.append(
            f"<consciousness level=\"{level}\" active_sensors=\"{active_sensors}\" />"
        )

        # ── Pending approvals ───────────────────────────────────────────────────
        pending = await self._permissions.pending_count()
        if pending > 0:
            sections.append(
                f"<pending_approvals count=\"{pending}\">Actions awaiting human approval</pending_approvals>"
            )

        if not sections:
            return ""

        return "<daemon_context>\n" + "\n".join(sections) + "\n</daemon_context>"


def _escape(text: str) -> str:
    """Minimal XML escape for context strings."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
