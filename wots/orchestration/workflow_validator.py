"""Workflow validation (spec v2 §6): on load, and when an office activates a workflow."""
from __future__ import annotations

from .workflow_loader import WorkflowDef


def validate_workflow(wf: WorkflowDef, known_types: set[str]) -> list[str]:
    """Problems with the definition itself. Empty list = valid."""
    errors: list[str] = []
    if wf.initial not in wf.states:
        errors.append(f"initial state {wf.initial} isn't defined")
    for a, b in wf.transitions:
        for s in (a, b):
            if s not in wf.states:
                errors.append(f"transition {a} -> {b} uses undefined state {s}")
    for state in wf.states.values():
        if state.owner in {"type", "assign"} and state.value not in known_types:
            errors.append(f"{state.name} references unknown employee type '{state.value}'")
        if state.owner != "terminal" and not wf.outgoing(state.name):
            errors.append(f"{state.name} isn't terminal but has no way out")
        if state.owner == "terminal" and wf.outgoing(state.name):
            errors.append(f"terminal state {state.name} has outgoing transitions")
        if state.owner == "assign" and len(wf.outgoing(state.name)) != 1:
            errors.append(f"{state.name} assigns work, so it needs exactly one next state")
        if state.skip_if_missing:
            if not state.skip_to or (state.name, state.skip_to) not in wf.transitions:
                errors.append(f"{state.name} has skip_if_missing but no valid skip_to transition")
    # Every state reachable from the initial one
    seen, frontier = {wf.initial}, [wf.initial]
    while frontier:
        for nxt in wf.outgoing(frontier.pop()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    for name in wf.states:
        if name not in seen:
            errors.append(f"{name} can't be reached from {wf.initial}")
    for type_key, states in wf.wip.items():
        if type_key not in known_types:
            errors.append(f"WIP rule for unknown employee type '{type_key}'")
        errors += [f"WIP rule lists undefined state {s}" for s in states if s not in wf.states]
    if wf.fix_loop:
        for key in ("counter_on", "escalate_to"):
            if wf.fix_loop.get(key) not in wf.states:
                errors.append(f"fix_loop.{key} isn't a defined state")
    return errors


def activation_problems(wf: WorkflowDef, hired_types: set[str], type_names: dict[str, str],
                        allow_missing: set[str] = frozenset()) -> list[str]:
    """Why an office can't run this workflow with its current team (spec v2 §6)."""
    problems = []
    for type_key, states in sorted(wf.required_types().items()):
        if type_key not in hired_types and type_key not in allow_missing:
            name = type_names.get(type_key, type_key)
            article = "an" if name[:1].lower() in "aeiou" else "a"
            problems.append(f"The {wf.key.replace('_', ' ')} workflow needs {article} {name} (for {', '.join(states)}). "
                            "Hire one or pick a different template.")
    return problems
