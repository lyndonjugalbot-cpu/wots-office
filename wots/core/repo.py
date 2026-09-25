"""Org-scoped data access (spec v2 §2.3).

Every query on a tenant table must filter by org_id. `scoped()` builds such queries, and
`install_query_guard()` makes any query that forgets raise TenancyViolation. The guard is on in
every runtime (and in tests). Deliberate cross-office reads (a user's memberships, Atlas listing
offices) opt out with `.execution_options(cross_org=True)`.
"""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.util import find_tables

from .context import OrgContext
from .models import TENANT_TABLES

T = TypeVar("T")


class TenancyViolation(RuntimeError):
    pass


class NotFound(LookupError):
    pass


def scoped(model: type[T], ctx: OrgContext) -> Select:
    return select(model).where(model.org_id == ctx.org_id)


def one_or_404(session: Session, model: type[T], ctx: OrgContext, **filters) -> T:
    query = scoped(model, ctx)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    row = session.scalars(query).first()
    if row is None:
        raise NotFound(f"{model.__name__} not found in this office")
    return row


_installed = False


def install_query_guard() -> None:
    """Fail loudly on any SELECT/UPDATE/DELETE of a tenant table without an org_id condition."""
    global _installed
    if _installed:
        return
    _installed = True

    @event.listens_for(Session, "do_orm_execute")
    def _check(state):
        if state.is_insert or state.execution_options.get("cross_org"):
            return
        stmt = state.statement
        where = getattr(stmt, "whereclause", None)
        tables = {t.name for t in find_tables(stmt, include_crud=True, include_joins=True)}
        touched = tables & TENANT_TABLES
        if touched and (where is None or "org_id" not in str(where)):
            raise TenancyViolation(
                f"Query on {', '.join(sorted(touched))} has no org_id filter. Use repo.scoped() / one_or_404(), "
                "or .execution_options(cross_org=True) for a deliberate cross-office read.")
