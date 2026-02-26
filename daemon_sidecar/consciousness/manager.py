"""Consciousness manager — sleep/wake state machine for DAEMON."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from daemon_sidecar.config import ConsciousnessConfig
from daemon_sidecar.models import PowerLevel, SensorType

logger = logging.getLogger(__name__)

# Sensors active at each power level
ACTIVE_SENSORS: dict[PowerLevel, list[SensorType]] = {
    PowerLevel.DEEP_SLEEP: [],
    PowerLevel.LIGHT_SLEEP: [SensorType.AUDIO],
    PowerLevel.AMBIENT: [SensorType.AUDIO, SensorType.SCREEN],
    PowerLevel.ENGAGED: [SensorType.AUDIO, SensorType.SCREEN],
    PowerLevel.FOCUSED: [SensorType.AUDIO, SensorType.SCREEN, SensorType.VIDEO],
}


class ConsciousnessManager:
    """
    Manages the agent's attention / power state.

    States (lowest → highest power):
      DEEP_SLEEP → LIGHT_SLEEP → AMBIENT → ENGAGED → FOCUSED
    """

    def __init__(self, config: ConsciousnessConfig):
        self._cfg = config
        self._level: PowerLevel = PowerLevel.AMBIENT
        self._last_event: datetime = datetime.utcnow()
        self._last_significant_event: datetime | None = None
        self._idle_task: asyncio.Task | None = None

    @property
    def current_level(self) -> PowerLevel:
        return self._level

    @property
    def last_significant_event(self) -> str | None:
        if self._last_significant_event:
            return self._last_significant_event.isoformat()
        return None

    def active_sensors(self) -> list[str]:
        return [s.value for s in ACTIVE_SENSORS.get(self._level, [])]

    async def force_wake(self, level: PowerLevel = PowerLevel.ENGAGED) -> None:
        logger.info("Consciousness: forcing wake to %s", level)
        self._level = level
        self._last_event = datetime.utcnow()
        self._schedule_idle_check()

    async def force_sleep(self) -> None:
        logger.info("Consciousness: forcing deep sleep")
        self._level = PowerLevel.DEEP_SLEEP
        if self._idle_task:
            self._idle_task.cancel()

    async def on_significant_event(self) -> None:
        """Called when a high-salience perception event occurs."""
        self._last_significant_event = datetime.utcnow()
        if self._level in (PowerLevel.DEEP_SLEEP, PowerLevel.LIGHT_SLEEP):
            await self.force_wake(PowerLevel.AMBIENT)

    async def on_heartbeat(self, event: dict[str, Any]) -> None:
        """Called on each OpenClaw heartbeat."""
        self._last_event = datetime.utcnow()

    def _schedule_idle_check(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(self._idle_loop())

    async def _idle_loop(self) -> None:
        """Gradually reduce power level when idle."""
        while True:
            await asyncio.sleep(60)
            idle_seconds = (datetime.utcnow() - self._last_event).total_seconds()

            if idle_seconds > self._cfg.deep_sleep_timeout_seconds:
                if self._level != PowerLevel.DEEP_SLEEP:
                    logger.info("Consciousness: deep sleep (idle %ds)", idle_seconds)
                    self._level = PowerLevel.DEEP_SLEEP
            elif idle_seconds > self._cfg.idle_timeout_seconds:
                if self._level not in (PowerLevel.DEEP_SLEEP, PowerLevel.LIGHT_SLEEP):
                    logger.info("Consciousness: light sleep (idle %ds)", idle_seconds)
                    self._level = PowerLevel.LIGHT_SLEEP
