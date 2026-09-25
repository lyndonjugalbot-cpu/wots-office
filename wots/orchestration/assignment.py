"""Assignment with WIP limits (spec v2 §7.2).

capacity = max_wip minus the employee's items in the workflow's active states.
  batch   -> new work only once ALL its current items are APPROVED or have left the pipeline
             (the CEO's "2 at a time, then 2 more" rule)
  rolling -> new work whenever it's below max_wip
Tie-break between employees with capacity: fewest items assigned in the last 7 days, then name.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.context import OrgContext
from ..core.models import Event, WorkItem


def active_count(s: Session, ctx: OrgContext, employee_id: str, active_states: list[str]) -> int:
    return s.scalar(select(func.count()).select_from(WorkItem).where(
        WorkItem.org_id == ctx.org_id, WorkItem.assigned_employee_id == employee_id,
        WorkItem.status.in_(active_states))) or 0


def capacity(s: Session, ctx: OrgContext, employee_id: str, config: dict, active_states: list[str]) -> int:
    max_wip = int(config.get("max_wip", 2))
    active = active_count(s, ctx, employee_id, active_states)
    if config.get("wip_mode", "batch") == "batch":
        return max_wip if active == 0 else 0
    return max(0, max_wip - active)


def recent_load(s: Session, ctx: OrgContext, employee_id: str, since: datetime, assigned_state: str) -> int:
    """Distinct items this employee was assigned since `since`."""
    return s.scalar(select(func.count(func.distinct(Event.work_item_id))).join(
        WorkItem, WorkItem.id == Event.work_item_id).where(
        Event.org_id == ctx.org_id, WorkItem.org_id == ctx.org_id, Event.to_status == assigned_state,
        Event.ts >= since, WorkItem.assigned_employee_id == employee_id)) or 0


def fairness_since(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
