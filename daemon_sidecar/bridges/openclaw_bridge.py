"""
Bridge between DAEMON sidecar and OpenClaw gateway.

Phase 3 update:
- Outbound message queue: messages buffered while disconnected, flushed on reconnect
- context_injection: formats and sends <daemon_context> XML
- bridge_status(): returns connection health for the /api/bridge/status endpoint
- Proper reconnect with exponential backoff (2 → 4 → 8 → 16 → 32s)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("websockets package not installed; OpenClaw bridge disabled")

# Maximum number of messages to queue while disconnected
_MAX_QUEUE = 50


class OpenClawBridge:
    """
    Outbound bridge: DAEMON sidecar → OpenClaw gateway (WebSocket).

    Integration Point 2: proactive messages and context injections are
    delivered through here so they appear in the user's preferred channel.
    """

    def __init__(self, gateway_url: str = "ws://localhost:18789"):
        self.gateway_url = gateway_url
        self._ws = None
        self._connected = False
        self._reconnecting = False
        self._queue: deque[dict[str, Any]] = deque(maxlen=_MAX_QUEUE)
        self._connected_at: datetime | None = None
        self._messages_sent: int = 0

    async def connect(self) -> None:
        """Connect to OpenClaw's Gateway WebSocket."""
        if not _WS_AVAILABLE:
            logger.warning("OpenClaw bridge: websockets not available")
            return
        try:
            self._ws = await websockets.connect(
                self.gateway_url,
                ping_interval=30,
                ping_timeout=10,
            )
            self._connected = True
            self._connected_at = datetime.utcnow()
            logger.info("OpenClaw bridge: connected to %s", self.gateway_url)
            asyncio.create_task(self._keepalive())
            # Flush any queued messages
            await self._flush_queue()
        except Exception as exc:
            logger.warning("OpenClaw bridge: connect failed (%s) — scheduling retry", exc)
            if not self._reconnecting:
                asyncio.create_task(self._reconnect_loop())

    def status(self) -> dict[str, Any]:
        """Return connection health info for the /api/bridge/status endpoint."""
        return {
            "connected": self._connected,
            "gateway_url": self.gateway_url,
            "connected_at": self._connected_at.isoformat() if self._connected_at else None,
            "messages_sent": self._messages_sent,
            "queued_messages": len(self._queue),
        }

    # ── Public send methods ────────────────────────────────────────────────────

    async def send_proactive_message(
        self,
        message: str,
        priority: str = "normal",
        actions: list[str] | None = None,
    ) -> None:
        """
        Send a proactive message to the user through OpenClaw's channels.

        DAEMON detects important event → salience filter → proactive engine
        → this bridge → WhatsApp / Telegram / Slack / etc.
        """
        payload: dict[str, Any] = {
            "type": "daemon_proactive",
            "message": message,
            "priority": priority,
        }
        if actions:
            payload["actions"] = actions
        await self._send(payload)

    async def inject_context(self, context: str) -> None:
        """
        Inject a <daemon_context> XML block into OpenClaw's next LLM call.

        Called before each agent response so the LLM is informed by current
        environmental context (audio, screen, memories).
        """
        if not context:
            return
        payload = {
            "type": "daemon_context_injection",
            "context": context,
        }
        await self._send(payload)

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._connected or self._ws is None:
            logger.debug(
                "OpenClaw bridge: offline — queuing %s (queue depth=%d)",
                payload.get("type"),
                len(self._queue),
            )
            self._queue.append(payload)
            if not self._reconnecting:
                asyncio.create_task(self._reconnect_loop())
            return

        try:
            await self._ws.send(json.dumps(payload))
            self._messages_sent += 1
            logger.debug("OpenClaw bridge: sent %s", payload.get("type"))
        except Exception as exc:
            logger.warning("OpenClaw bridge: send failed (%s) — queuing message", exc)
            self._queue.append(payload)
            self._connected = False
            if not self._reconnecting:
                asyncio.create_task(self._reconnect_loop())

    async def _flush_queue(self) -> None:
        """Send all buffered messages after reconnect."""
        flushed = 0
        while self._queue and self._connected:
            payload = self._queue.popleft()
            try:
                await self._ws.send(json.dumps(payload))
                self._messages_sent += 1
                flushed += 1
            except Exception as exc:
                logger.warning("OpenClaw bridge: flush failed (%s)", exc)
                self._queue.appendleft(payload)
                break
        if flushed:
            logger.info("OpenClaw bridge: flushed %d queued messages", flushed)

    async def _keepalive(self) -> None:
        """Periodic ping to detect silent disconnections."""
        while self._connected and self._ws is not None:
            await asyncio.sleep(30)
            try:
                pong = await self._ws.ping()
                await asyncio.wait_for(pong, timeout=10)
            except Exception:
                logger.warning("OpenClaw bridge: keepalive failed — reconnecting")
                self._connected = False
                if not self._reconnecting:
                    asyncio.create_task(self._reconnect_loop())
                break

    async def _reconnect_loop(self) -> None:
        self._reconnecting = True
        delays = [2, 4, 8, 16, 32, 60]
        for i, delay in enumerate(delays):
            logger.info(
                "OpenClaw bridge: reconnect attempt %d/%d in %ds",
                i + 1, len(delays), delay,
            )
            await asyncio.sleep(delay)
            try:
                await self.connect()
                if self._connected:
                    self._reconnecting = False
                    return
            except Exception as exc:
                logger.debug("OpenClaw bridge: reconnect attempt failed: %s", exc)
        logger.error(
            "OpenClaw bridge: gave up after %d reconnect attempts; "
            "bridge will operate in queue-only mode",
            len(delays),
        )
        self._reconnecting = False
