"""Proactive engagement engine — decides when to surface insights to the user."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta

from daemon_sidecar.config import ProactiveConfig
from daemon_sidecar.models import PerceptionEvent

logger = logging.getLogger(__name__)


class ProactiveEngine:
    """
    Manages proactive interrupts to the user.

    Enforces an interrupt budget (max N messages per hour) and respects
    quiet hours so the agent doesn't bother the user at night.
    """

    def __init__(self, config: ProactiveConfig):
        self._cfg = config
        # Timestamps of recent interrupts for budget enforcement
        self._interrupt_log: deque[datetime] = deque()
        self._bridge = None  # injected after construction

    def set_bridge(self, bridge) -> None:  # noqa: ANN001
        self._bridge = bridge

    async def consider(self, event: PerceptionEvent) -> None:
        """Evaluate whether this event warrants a proactive message."""
        if not self._cfg.enabled:
            return
        if self._in_quiet_hours():
            logger.debug("Proactive: quiet hours, suppressing")
            return
        if not self._within_budget():
            logger.debug("Proactive: interrupt budget exhausted")
            return
        if event.salience < self._cfg.min_salience_threshold:
            return

        await self._send(event)

    async def _send(self, event: PerceptionEvent) -> None:
        message = self._craft_message(event)
        self._record_interrupt()
        logger.info("Proactive: sending message (salience=%.2f)", event.salience)
        if self._bridge:
            await self._bridge.send_proactive_message(message)

    def _craft_message(self, event: PerceptionEvent) -> str:
        snippet = event.transcription[:200] if event.transcription else "(no text)"
        return f"[DAEMON] Detected important event ({event.sensor_type.value}): {snippet}"

    def _in_quiet_hours(self) -> bool:
        hour = datetime.now().hour
        start = self._cfg.quiet_hours_start
        end = self._cfg.quiet_hours_end
        if start > end:  # spans midnight
            return hour >= start or hour < end
        return start <= hour < end

    def _within_budget(self) -> bool:
        cutoff = datetime.utcnow() - timedelta(hours=1)
        # Drop old entries
        while self._interrupt_log and self._interrupt_log[0] < cutoff:
            self._interrupt_log.popleft()
        return len(self._interrupt_log) < self._cfg.interrupt_budget_per_hour

    def _record_interrupt(self) -> None:
        self._interrupt_log.append(datetime.utcnow())
