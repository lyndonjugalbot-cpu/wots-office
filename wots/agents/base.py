"""What every agent implements (spec §7), and loading agents from config/agents.yaml.

Agents never call board.transition(); they return an AgentResult and Atlas applies it.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..core.config import AgentConfig, Config
from ..core.llm import LLM
from ..core.models import Lead

log = logging.getLogger(__name__)


@dataclass
class ArtifactOut:
    kind: str  # copy|brief|logo|hero|site|ad_variant|qa_report|pitch
    path: str  # relative to the lead's folder


@dataclass
class AgentResult:
    """next_status None means "no change": the lead is simply released."""
    next_status: str | None
    note: str | None = None
    artifacts: list[ArtifactOut] = field(default_factory=list)
    # Lead fields to set along with the status change (e.g. Ledger's enrichment)
    updates: dict[str, Any] = field(default_factory=dict)
    # Hawk's verdict: {"passed": bool, "issues": [...]}, stored as a qa_reports row
    qa_report: dict[str, Any] | None = None


@dataclass
class AgentContext:
    config: Config
    lead_dir: Path
    dry_run: bool
    llm: LLM | None
    model: str | None
    # The latest QA report or CEO notes when a lead comes back for a fix (spec §6.3)
    feedback: str | None = None


class Agent(Protocol):
    name: str
    config: AgentConfig

    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult: ...


class BaseAgent:
    """Convenience base: instances differ only by config (name, style profile, model)."""

    def __init__(self, config: AgentConfig):
        self.name = config.name
        self.config = config

    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError


def load_agents(config: Config) -> dict[str, Agent]:
    """Instantiate every enabled agent whose class exists. Missing classes are skipped with a warning,
    so agents can be switched on phase by phase."""
    agents: dict[str, Agent] = {}
    for name, cfg in config.agents.agents.items():
        if not cfg.enabled:
            continue
        module_name, _, class_name = cfg.cls.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError):
            log.warning("Agent %s is enabled but %s isn't implemented yet; skipping", name, cfg.cls)
            continue
        agents[name] = cls(cfg)
    return agents
