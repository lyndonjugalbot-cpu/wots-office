"""The platform catalogue: employee types, workflows and office templates, loaded from files and
synced into their (versioned) tables on startup."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..employees.registry import EmployeeTypeDef, load_types
from ..orchestration.workflow_loader import WorkflowDef, WorkflowError, load_office_templates, load_workflows
from ..orchestration.workflow_validator import validate_workflow
from .models import EmployeeType, OfficeTemplate, WorkflowDef as WorkflowRow


@dataclass
class Catalogue:
    types: dict[str, EmployeeTypeDef]
    workflows: dict[str, WorkflowDef]
    templates: dict[str, dict]

    @classmethod
    def load(cls) -> "Catalogue":
        cat = cls(load_types(), load_workflows(), load_office_templates())
        cat.validate()
        return cat

    def validate(self) -> None:
        problems = [f"{key}: {e}" for key, wf in self.workflows.items() for e in validate_workflow(wf, set(self.types))]
        for key, template in self.templates.items():
            problems += [f"template {key}: unknown workflow {w}" for w in template.get("workflows", []) if w not in self.workflows]
            problems += [f"template {key}: unknown employee type {m['type']}" for m in template.get("recommended_team", [])
                         if m["type"] not in self.types]
        if problems:
            raise WorkflowError("Catalogue problems:\n  " + "\n  ".join(problems))

    def type_names(self) -> dict[str, str]:
        return {k: t.display_name for k, t in self.types.items()}

    def sync(self, sessions: sessionmaker[Session]) -> None:
        """Insert any type/workflow/template version the database doesn't have yet."""
        with sessions.begin() as s:
            have = set(s.execute(select(EmployeeType.key, EmployeeType.version)).all())
            for t in self.types.values():
                if (t.key, t.version) not in have:
                    s.add(EmployeeType(key=t.key, version=t.version, display_name=t.display_name, category=t.category,
                                       description=t.description, impl_path=t.impl, task_kinds=t.task_kinds,
                                       default_model=t.default_model, config_schema=t.config_schema,
                                       risk_level=t.risk_level, plans=t.plans,
                                       est_credits_per_task=t.est_credits_per_task, status=t.status))
            have = set(s.execute(select(WorkflowRow.key, WorkflowRow.version)).all())
            for w in self.workflows.values():
                if (w.key, w.version) not in have:
                    s.add(WorkflowRow(key=w.key, version=w.version, definition=w.raw))
            have = set(s.execute(select(OfficeTemplate.key, OfficeTemplate.version)).all())
            for key, t in self.templates.items():
                if (key, t.get("version", 1)) not in have:
                    s.add(OfficeTemplate(key=key, version=t.get("version", 1), definition=t))
