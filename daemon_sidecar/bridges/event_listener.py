"""
OpenClaw Gateway event listener.

Captures all events OpenClaw emits (tool executions, LLM calls, messages)
and routes them to the side-effect ledger, memory store, and cost tracker.

Integration Point 3: OpenClaw → Sidecar via WebSocket events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from daemon_sidecar.models import LedgerEntry, SideEffectType

if TYPE_CHECKING:
    from daemon_sidecar.consciousness.manager import ConsciousnessManager
    from daemon_sidecar.memory.episodic import EpisodicMemory
    from daemon_sidecar.safety.ledger import SideEffectLedger

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class CostTracker:
    """Simple in-memory cost accumulator."""

    # Approximate cost per 1M tokens (USD) — adjust as needed
    _COSTS: dict[str, tuple[float, float]] = {
        "claude-opus-4-6": (15.0, 75.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-haiku-4-5": (0.25, 1.25),
    }

    def __init__(self):
        self._entries: list[dict[str, Any]] = []

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        in_cost, out_cost = self._COSTS.get(model, (3.0, 15.0))
        cost = (input_tokens / 1_000_000 * in_cost) + (output_tokens / 1_000_000 * out_cost)
        self._entries.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "timestamp": datetime.utcnow(),
        })

    def current_hour_total(self) -> float:
        cutoff = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        return sum(e["cost_usd"] for e in self._entries if e["timestamp"] >= cutoff)

    def today_total(self) -> float:
        cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(e["cost_usd"] for e in self._entries if e["timestamp"] >= cutoff)


class OpenClawEventListener:
    """
    Listens to OpenClaw Gateway WebSocket events.

    Captures:
    - All tool executions  → side-effect ledger
    - All LLM calls        → cost tracker
    - User messages        → episodic memory
    - Agent messages       → episodic memory
    - Heartbeat events     → consciousness coordination
    """

    def __init__(
        self,
        gateway_url: str,
        ledger: "SideEffectLedger",
        memory: "EpisodicMemory",
        costs: CostTracker,
        consciousness: "ConsciousnessManager",
    ):
        self.gateway_url = gateway_url
        self.ledger = ledger
        self.memory = memory
        self.costs = costs
        self.consciousness = consciousness

    async def listen(self) -> None:
        if not _WS_AVAILABLE:
            logger.warning("Event listener: websockets not available, skipping")
            return
        while True:
            try:
                async with websockets.connect(self.gateway_url) as ws:
                    logger.info("Event listener: connected to %s", self.gateway_url)
                    async for raw_message in ws:
                        try:
                            event = json.loads(raw_message)
                            await self._handle_event(event)
                        except json.JSONDecodeError:
                            logger.debug("Event listener: received non-JSON message")
            except Exception as exc:
                logger.warning("Event listener: connection lost (%s), reconnecting in 5s", exc)
                await asyncio.sleep(5)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")

        if event_type == "tool_execution":
            await self.ledger.record(LedgerEntry(
                tool_name=event.get("tool", "unknown"),
                tool_input=event.get("input", {}),
                tool_output=str(event.get("output", ""))[:2000],
                permission_tier=self._classify_tier(event),
                side_effect_type=self._classify_side_effect(event),
                timestamp=datetime.utcnow(),
            ))

        elif event_type == "llm_call":
            self.costs.record(
                model=event.get("model", "unknown"),
                input_tokens=event.get("input_tokens", 0),
                output_tokens=event.get("output_tokens", 0),
            )

        elif event_type == "user_message":
            await self.memory.record_interaction(
                role="user",
                content=event.get("content", ""),
                channel=event.get("channel"),
            )

        elif event_type == "agent_message":
            await self.memory.record_interaction(
                role="agent",
                content=event.get("content", ""),
                channel=event.get("channel"),
            )

        elif event_type == "heartbeat":
            await self.consciousness.on_heartbeat(event)

    @staticmethod
    def _classify_tier(event: dict[str, Any]) -> int:
        tool = event.get("tool", "")
        destructive = {"delete", "remove", "drop", "truncate", "rm"}
        if any(d in tool.lower() for d in destructive):
            return 3
        write_tools = {"write", "create", "post", "send", "push"}
        if any(w in tool.lower() for w in write_tools):
            return 1
        return 0

    @staticmethod
    def _classify_side_effect(event: dict[str, Any]) -> SideEffectType:
        tool = event.get("tool", "").lower()
        mapping = {
            "read": SideEffectType.FILE_READ,
            "write": SideEffectType.FILE_WRITE,
            "delete": SideEffectType.FILE_DELETE,
            "http_get": SideEffectType.HTTP_GET,
            "http_post": SideEffectType.HTTP_POST,
            "shell": SideEffectType.SHELL_RUN,
            "email": SideEffectType.EMAIL_SEND,
            "message": SideEffectType.MESSAGE_SEND,
        }
        for key, side_effect in mapping.items():
            if key in tool:
                return side_effect
        return SideEffectType.UNKNOWN
