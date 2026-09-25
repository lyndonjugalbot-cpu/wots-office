"""Workflows as data (spec v2 §6): load wots/workflows/*.yaml into WorkflowDef objects.

State owners:
  {type: X}    any employee of type X may take it
  assigned     only the item's assigned employee
  {assign: X}  Atlas assigns an employee of type X (WIP rules), then moves to the next state
  {gate: role} waits for a human with that role
  system       Atlas itself (timers)
  terminal     done
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "workflows"
OFFICE_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "offices"


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class StateDef:
    name: str
    owner: str  # type | assigned | assign | gate | system | terminal
    value: str | None = None  # employee type or gate role
    task: str | None = None
    skip_if_missing: bool = False
    skip_to: str | None = None
    note: str | None = None

    @property
    def employee_owned(self) -> bool:
        return self.owner in {"type", "assigned"}


@dataclass
class WorkflowDef:
    key: str
    version: int
    work_item_kind: str
    initial: str
    states: dict[str, StateDef]
    transitions: set[tuple[str, str]]
    wip: dict[str, list[str]] = field(default_factory=dict)  # employee type -> active states
    fix_loop: dict | None = None
    followup: dict | None = None
    raw: dict = field(default_factory=dict)

    @property
    def escalate_to(self) -> str | None:
        return (self.fix_loop or {}).get("escalate_to")

    def outgoing(self, state: str) -> list[str]:
        return sorted(t for f, t in self.transitions if f == state)

    def is_allowed(self, from_state: str, to_state: str) -> bool:
        if (from_state, to_state) in self.transitions:
            return True
        # Retries exhausted: any state an employee works on can escalate (spec v2 §7.5)
        state = self.states.get(from_state)
        return bool(state and state.employee_owned and self.escalate_to and to_state == self.escalate_to)

    def required_types(self) -> dict[str, list[str]]:
        """Employee type -> states that need it (states with skip_if_missing excluded)."""
        need: dict[str, list[str]] = {}
        for state in self.states.values():
            if state.owner in {"type", "assign"} and not state.skip_if_missing:
                need.setdefault(state.value, []).append(state.name)
        return need

    def gates(self) -> set[str]:
        return {s.name for s in self.states.values() if s.owner == "gate"}

    def start_hop(self, state: str) -> str | None:
        """For an `assigned` state with exactly one move into another `assigned` state (ASSIGNED -> BUILDING,
        NEEDS_FIX -> BUILDING): the state the employee moves to when it starts work."""
        targets = [t for t in self.outgoing(state) if self.states[t].owner == "assigned" and t != state]
        return targets[0] if self.states[state].owner == "assigned" and len(targets) == 1 else None


def _state(name: str, raw) -> StateDef:
    if raw == "terminal":
        return StateDef(name, "terminal")
    if not isinstance(raw, dict) or "owner" not in raw:
        raise WorkflowError(f"State {name} needs an owner")
    owner = raw["owner"]
    extra = {k: raw.get(k) for k in ("task", "skip_to", "note")}
    extra["skip_if_missing"] = bool(raw.get("skip_if_missing", False))
    if isinstance(owner, str) and owner in {"assigned", "system"}:
        return StateDef(name, owner, None, **extra)
    if isinstance(owner, dict) and len(owner) == 1:
        (kind, value), = owner.items()
        if kind in {"type", "assign", "gate"}:
            return StateDef(name, kind, str(value), **extra)
    raise WorkflowError(f"State {name}: unknown owner {owner!r}")


def parse_workflow(raw: dict) -> WorkflowDef:
    try:
        states = {name: _state(name, value) for name, value in raw["states"].items()}
        rules = raw.get("rules") or {}
        return WorkflowDef(
            key=raw["key"], version=int(raw.get("version", 1)), work_item_kind=raw.get("work_item_kind", "lead"),
            initial=raw["initial"], states=states,
            transitions={(str(a), str(b)) for a, b in raw.get("transitions", [])},
            wip={t: list(v.get("active_states", [])) for t, v in (rules.get("wip") or {}).items()},
            fix_loop=rules.get("fix_loop"), followup=rules.get("followup"), raw=raw,
        )
    except KeyError as e:
        raise WorkflowError(f"Workflow is missing {e}") from e


def load_workflows(directory: Path = WORKFLOWS_DIR) -> dict[str, WorkflowDef]:
    return {w.key: w for w in (parse_workflow(yaml.safe_load(p.read_text())) for p in sorted(directory.glob("*.yaml")))}


def load_office_templates(directory: Path = OFFICE_TEMPLATES_DIR) -> dict[str, dict]:
    return {t["key"]: t for t in (yaml.safe_load(p.read_text()) for p in sorted(directory.glob("*.yaml")))}
