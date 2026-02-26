"""Permission tier system for DAEMON sidecar."""

from __future__ import annotations

import asyncio
import logging

from daemon_sidecar.config import SafetyConfig
from daemon_sidecar.models import PermissionCheckRequest, PermissionResult

logger = logging.getLogger(__name__)


class PermissionGate:
    """
    Classifies actions into permission tiers and decides approval.

    Tier 0: Auto-approve, no log needed (read-only, harmless)
    Tier 1: Auto-approve, log the action
    Tier 2: Requires explicit human approval (consequential)
    Tier 3: Destructive — hard stop, full explanation required
    """

    def __init__(self, config: SafetyConfig):
        self._cfg = config
        self._pending: list[dict] = []

    async def check(self, request: PermissionCheckRequest) -> PermissionResult:
        tier = self._classify(request)
        logger.info(
            "Permission check: tool=%s tier=%d side_effects=%s",
            request.tool,
            tier,
            request.side_effects,
        )

        if tier == 0:
            return PermissionResult(approved=True, tier=0)
        if tier == 1:
            return PermissionResult(approved=True, tier=1)
        if tier == 2:
            self._pending.append({"action": request.action, "tool": request.tool})
            return PermissionResult(
                approved=False,
                tier=2,
                reason="requires_human_approval",
            )
        # tier == 3
        return PermissionResult(
            approved=False,
            tier=3,
            reason="destructive_action",
        )

    def _classify(self, request: PermissionCheckRequest) -> int:
        """Return the highest (most restrictive) tier matching any side effect."""
        effects = request.side_effects or [f"unknown:{request.tool}"]
        highest = 0
        for effect in effects:
            tier = self._tier_for_effect(effect)
            if tier > highest:
                highest = tier
        return highest

    def _tier_for_effect(self, effect: str) -> int:
        if any(p in effect for p in self._cfg.tier3_patterns):
            return 3
        if any(p in effect for p in self._cfg.tier2_patterns):
            return 2
        if any(p in effect for p in self._cfg.tier1_patterns):
            return 1
        return 0

    async def pending_count(self) -> int:
        return len(self._pending)
