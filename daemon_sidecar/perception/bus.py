"""
Perception bus — coordinates sensors, salience filter, and proactive routing.

Phase 2 update: wires real AudioSensor, ScreenSensor, VideoSensor and uses
EmbeddingSalienceScorer instead of the keyword-only fallback.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from daemon_sidecar.config import PerceptionConfig
from daemon_sidecar.memory.embeddings import EmbeddingSalienceScorer
from daemon_sidecar.models import PerceptionEvent, SensorType

if TYPE_CHECKING:
    from daemon_sidecar.bridges.openclaw_bridge import OpenClawBridge
    from daemon_sidecar.consciousness.manager import ConsciousnessManager
    from daemon_sidecar.proactive.engine import ProactiveEngine

logger = logging.getLogger(__name__)


class PerceptionBus:
    """
    Central bus that:
    1. Starts real sensor loops (audio / screen / video)
    2. Scores every incoming event with the embedding salience scorer
    3. Routes high-salience events → consciousness manager + proactive engine
    4. Keeps an in-memory ring buffer of the 1 000 most recent events
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
        self._salience = EmbeddingSalienceScorer()
        self._events: list[PerceptionEvent] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        self._running = True

        # Initialise the embedding scorer (loads model in thread pool)
        await self._salience.initialize()

        sensors_started: list[str] = []

        if self._cfg.audio_enabled:
            from daemon_sidecar.perception.audio_sensor import AudioSensor
            audio = AudioSensor(
                whisper_url=self._cfg.whisper_url,
                vad_aggressiveness=int(self._cfg.vad_threshold * 3),
                on_transcription=self._on_audio,
            )
            self._tasks.append(asyncio.create_task(audio.run()))
            sensors_started.append("audio")

        if self._cfg.screen_enabled:
            from daemon_sidecar.perception.screen_sensor import ScreenSensor
            screen = ScreenSensor(
                interval_seconds=self._cfg.screen_interval_seconds,
                on_screen_event=self._on_screen,
            )
            self._tasks.append(asyncio.create_task(screen.run()))
            sensors_started.append("screen")

        if self._cfg.video_enabled:
            from daemon_sidecar.perception.video_sensor import VideoSensor
            video = VideoSensor(
                interval_seconds=self._cfg.screen_interval_seconds * 2,
                on_frame_event=self._on_video,
            )
            self._tasks.append(asyncio.create_task(video.run()))
            sensors_started.append("video")

        logger.info("PerceptionBus started: sensors=%s", sensors_started)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("PerceptionBus stopped")

    # ── Public query API (called by FastAPI routes) ────────────────────────────

    async def ingest(self, event: PerceptionEvent) -> None:
        """Accept a new perception event, score it, and act on it."""
        event.salience = self._salience.score(event.transcription)
        self._events.append(event)
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
        return [
            e for e in reversed(self._events)
            if q in e.transcription.lower()
        ][:limit]

    # ── Sensor callbacks (invoked by sensor objects) ───────────────────────────

    async def _on_audio(self, text: str, metadata: dict) -> None:
        event = PerceptionEvent(
            sensor_type=SensorType.AUDIO,
            transcription=text,
            metadata=metadata,
        )
        await self.ingest(event)
        logger.debug("Audio event ingested: %.80s", text)

    async def _on_screen(self, text: str, metadata: dict) -> None:
        event = PerceptionEvent(
            sensor_type=SensorType.SCREEN,
            transcription=text,
            metadata=metadata,
        )
        await self.ingest(event)
        logger.debug(
            "Screen event ingested (%s): %.60s",
            metadata.get("application", "?"),
            text[:60],
        )

    async def _on_video(self, description: str, metadata: dict) -> None:
        event = PerceptionEvent(
            sensor_type=SensorType.VIDEO,
            transcription=description,
            metadata=metadata,
        )
        await self.ingest(event)
        logger.debug("Video event ingested: %s", description)
