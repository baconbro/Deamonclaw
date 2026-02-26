"""
Bridge between DAEMON sidecar and OpenClaw gateway.

When the sidecar wants to proactively message the user,
it sends the message through OpenClaw so it appears in
their preferred channel (WhatsApp, Telegram, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("websockets package not installed; OpenClaw bridge disabled")


class OpenClawBridge:
    """
    Bridge between DAEMON sidecar and OpenClaw gateway.

    Integration Point 2: Sidecar → OpenClaw via WebSocket.
    """

    def __init__(self, gateway_url: str = "ws://localhost:18789"):
        self.gateway_url = gateway_url
        self._ws = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to OpenClaw's Gateway WebSocket."""
        if not _WS_AVAILABLE:
            logger.warning("OpenClaw bridge: websockets not available, skipping connect")
            return
        try:
            self._ws = await websockets.connect(self.gateway_url)
            self._connected = True
            logger.info("OpenClaw bridge: connected to %s", self.gateway_url)
            asyncio.create_task(self._keepalive())
        except Exception as exc:
            logger.warning("OpenClaw bridge: could not connect (%s) — will retry", exc)
            asyncio.create_task(self._reconnect_loop())

    async def send_proactive_message(
        self,
        message: str,
        priority: str = "normal",
        actions: list[str] | None = None,
    ) -> None:
        """
        Send a message to the user through OpenClaw's channels.

        DAEMON detects something important → salience filter scores it →
        proactive engine crafts a message → this bridge delivers it via
        WhatsApp / Telegram / Slack / etc.
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
        Inject perception context into OpenClaw's next LLM call.

        Allows the agent to answer user queries informed by what's
        happening in the user's environment right now.
        """
        payload = {
            "type": "daemon_context_injection",
            "context": context,
        }
        await self._send(payload)

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._connected or self._ws is None:
            logger.debug("OpenClaw bridge: not connected, dropping message")
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            logger.warning("OpenClaw bridge: send failed (%s)", exc)
            self._connected = False
            asyncio.create_task(self._reconnect_loop())

    async def _keepalive(self) -> None:
        while self._connected:
            await asyncio.sleep(30)
            try:
                await self._ws.ping()
            except Exception:
                self._connected = False
                asyncio.create_task(self._reconnect_loop())
                break

    async def _reconnect_loop(self) -> None:
        delays = [2, 4, 8, 16, 32]
        for delay in delays:
            await asyncio.sleep(delay)
            try:
                await self.connect()
                if self._connected:
                    return
            except Exception:
                pass
        logger.error("OpenClaw bridge: gave up reconnecting after %d attempts", len(delays))
