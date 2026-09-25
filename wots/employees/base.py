"""What every employee implements (spec v2 §7).

Employees never change an item's status themselves: they return an EmployeeResult, and Atlas
validates it against the workflow and applies it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..core.config import Config
from ..core.context import OrgContext
from ..core.metering import EmployeeLLM
from .registry import EmployeeTypeDef


@dataclass
class ArtifactOut:
    kind: str  # copy|brief|logo|hero|site|ad_variant|qa_report|pitch
    path: str  # relative to the item's folder


@dataclass
class EmployeeResult:
    """next_status None = no change; the item is simply released."""
    next_status: str | None
    note: str | None = None
    artifacts: list[ArtifactOut] = field(default_factory=list)
    updates: dict[str, Any] = field(default_factory=dict)  # lead profile / work item fields
    qa_report: dict[str, Any] | None = None  # {"passed": bool, "issues": [...]} -> a qa_reports row


@dataclass(frozen=True)
class EmployeeInfo:
    id: str
    name: str
    type: EmployeeTypeDef
    config: dict[str, Any]  # the hire's validated config (defaults filled in)


@dataclass
class EmployeeContext:
    org: OrgContext
    employee: EmployeeInfo
    config: Config  # platform config: settings, models, country rules
    item_dir: Path  # data/orgs/{org_id}/items/{item_id}/
    task: str
    dry_run: bool
    llm: EmployeeLLM | None
    model: str | None
    effort: str | None = None
    feedback: str | None = None  # latest QA report or CEO notes when an item comes back
    tools: Any = None  # this office's integration clients (wots.integrations.toolbox.Toolbox)


class Employee(Protocol):
    type_key: str

    def run(self, item: Any, task: str, ctx: EmployeeContext) -> EmployeeResult: ...


class BaseEmployee:
    """Instances differ only by their hire's name and config (style profile, WIP, model...)."""

    type_key = ""

    def __init__(self, info: EmployeeInfo):
        self.id = info.id
        self.name = info.name
        self.info = info
        self.config = info.config

    def run(self, item: Any, task: str, ctx: EmployeeContext) -> EmployeeResult:  # pragma: no cover - interface
        raise NotImplementedError
