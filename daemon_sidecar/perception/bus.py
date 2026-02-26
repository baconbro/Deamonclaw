"""Perception bus — coordinates sensors, salience filter, and proactive routing."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from daemon_sidecar.config import PerceptionConfig
from daemon_sidecar.models import PerceptionEvent, SensorType

if TYPE_CHECKING:
    from daemon_sidecar.consciousness.manager import ConsciousnessManager
    from daemon_sidecar.proactive.engine import ProactiveEngine
    from daemon_sidecar.bridges.openclaw_bridge import OpenClawBridge

logger = logging.getLogger(__name__)


class SalienceFilter:
    """
    Scores perception events for importance.

    Currently rule-based; can be replaced with an embedding similarity
    approach once the memory store has enough data.
    """

    # Keywords that boost salience
    HIGH_SALIENCE_KEYWORDS = [
        "urgent", "deadline", "emergency", "critical", "asap",
        "important", "meeting", "budget", "payment", "error",
    ]

    def score(self, event: PerceptionEvent) -> float:
        text = event.transcription.lower()
        score = 0.0
        for kw in self.HIGH_SALIENCE_KEYWORDS:
            if kw in text:
                score += 0.15
        # Clamp to [0.0, 1.0]
        return min(score, 1.0)


class PerceptionBus:
    """
    Central bus that receives events from sensors, scores them,
    and routes high-salience events to the proactive engine.
    """

    def __init__(
        self,
        config: PerceptionConfig,
        consciousness: "ConsciousnessManager",
        proactive: "ProactiveEngine",
        bridge: "OpenClawBridge",
    ):
        self._cfg = config
        self._consciousness = consciousness
        self._proactive = proactive
        self._bridge = bridge
        self._salience = SalienceFilter()
        self._events: list[PerceptionEvent] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        if self._cfg.audio_enabled:
            self._tasks.append(asyncio.create_task(self._audio_loop()))
        if self._cfg.screen_enabled:
            self._tasks.append(asyncio.create_task(self._screen_loop()))
        logger.info("PerceptionBus started (audio=%s screen=%s)", self._cfg.audio_enabled, self._cfg.screen_enabled)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("PerceptionBus stopped")

    async def ingest(self, event: PerceptionEvent) -> None:
        """Accept a new perception event, score it, and act on it."""
        event.salience = self._salience.score(event)
        self._events.append(event)

        # Keep last 1 000 events in memory
        if len(self._events) > 1000:
            self._events = self._events[-1000:]

        if event.salience >= 0.5:
            await self._consciousness.on_significant_event()
            await self._proactive.consider(event)

    async def get_recent(
        self,
        sensor_type: SensorType,
        minutes: int = 30,
    ) -> list[PerceptionEvent]:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        return [
            e for e in self._events
            if e.sensor_type == sensor_type and e.timestamp >= cutoff
        ]

    async def get_latest(self, sensor_type: SensorType) -> PerceptionEvent | None:
        for event in reversed(self._events):
            if event.sensor_type == sensor_type:
                return event
        return None

    async def get_salient_events(
        self,
        min_salience: float = 0.5,
        minutes: int = 60,
    ) -> list[PerceptionEvent]:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        return [
            e for e in self._events
            if e.salience >= min_salience and e.timestamp >= cutoff
        ]

    async def search(self, query: str, limit: int = 10) -> list[PerceptionEvent]:
        q = query.lower()
        return [e for e in reversed(self._events) if q in e.transcription.lower()][:limit]

    # ── Sensor loops (stubs — replace with real sensor implementations) ────────

    async def _audio_loop(self) -> None:
        """
        Audio sensor loop.

        In production this would:
        1. Capture audio from the microphone in chunks
        2. Run VAD (Voice Activity Detection) to detect speech
        3. Send speech chunks to the Whisper server at self._cfg.whisper_url
        4. Parse the transcription response
        5. Call self.ingest() with the resulting PerceptionEvent

        This stub emits a placeholder event every 60 seconds so the rest
        of the system can be tested without real audio hardware.
        """
        while self._running:
            await asyncio.sleep(60)

    async def _screen_loop(self) -> None:
        """
        Screen sensor loop.

        In production this would:
        1. Capture a screenshot every self._cfg.screen_interval_seconds
        2. Run OCR (e.g. pytesseract or easyocr) on the screenshot
        3. Extract the active application and window title
        4. Call self.ingest() with a SensorType.SCREEN PerceptionEvent

        This stub sleeps on the configured interval.
        """
        while self._running:
            await asyncio.sleep(self._cfg.screen_interval_seconds)
