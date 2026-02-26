"""Configuration loading for DAEMON sidecar."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class PerceptionConfig:
    audio_enabled: bool = True
    screen_enabled: bool = True
    video_enabled: bool = False
    whisper_url: str = "http://localhost:8500"
    vad_threshold: float = 0.5
    screen_interval_seconds: int = 30


@dataclass
class ConsciousnessConfig:
    idle_timeout_seconds: int = 300
    deep_sleep_timeout_seconds: int = 3600
    wake_on_audio: bool = True
    wake_on_message: bool = True


@dataclass
class ProactiveConfig:
    enabled: bool = True
    interrupt_budget_per_hour: int = 10
    min_salience_threshold: float = 0.6
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8


@dataclass
class SafetyConfig:
    # Side-effect patterns that auto-approve (tier 0)
    tier0_patterns: list[str] = field(default_factory=lambda: [
        "file:read", "http:get", "memory:read",
    ])
    # Patterns that log but approve (tier 1)
    tier1_patterns: list[str] = field(default_factory=lambda: [
        "file:write", "http:post", "shell:run",
    ])
    # Patterns requiring human approval (tier 2)
    tier2_patterns: list[str] = field(default_factory=lambda: [
        "email:send", "calendar:create", "message:send",
    ])
    # Patterns that are destructive (tier 3)
    tier3_patterns: list[str] = field(default_factory=lambda: [
        "file:delete", "purchase:make", "account:modify",
    ])


@dataclass
class DaemonConfig:
    memory_db_url: str = "postgresql://daemon:daemon@localhost:5433/daemon"
    openclaw_gateway_url: str = "ws://localhost:18789"
    api_port: int = 8400
    anthropic_api_key: str = ""
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    consciousness: ConsciousnessConfig = field(default_factory=ConsciousnessConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def load_config(path: str = "daemon.yaml") -> DaemonConfig:
    """Load config from YAML file, with env var overrides."""
    raw: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    cfg = DaemonConfig(
        memory_db_url=os.getenv("DATABASE_URL", raw.get("memory_db_url", DaemonConfig.memory_db_url)),
        openclaw_gateway_url=os.getenv(
            "OPENCLAW_GATEWAY_URL",
            raw.get("openclaw_gateway_url", DaemonConfig.openclaw_gateway_url),
        ),
        api_port=int(os.getenv("API_PORT", str(raw.get("api_port", DaemonConfig.api_port)))),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", raw.get("anthropic_api_key", "")),
    )

    if "perception" in raw:
        p = raw["perception"]
        cfg.perception = PerceptionConfig(**{k: v for k, v in p.items() if hasattr(PerceptionConfig, k)})
    if "consciousness" in raw:
        c = raw["consciousness"]
        cfg.consciousness = ConsciousnessConfig(**{k: v for k, v in c.items() if hasattr(ConsciousnessConfig, k)})
    if "proactive" in raw:
        pr = raw["proactive"]
        cfg.proactive = ProactiveConfig(**{k: v for k, v in pr.items() if hasattr(ProactiveConfig, k)})
    if "safety" in raw:
        s = raw["safety"]
        cfg.safety = SafetyConfig(**{k: v for k, v in s.items() if hasattr(SafetyConfig, k)})

    return cfg
