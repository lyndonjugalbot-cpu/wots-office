"""Offices, members, hiring and workflow activation (spec v2 §3, §6, §11).

Rules enforced here:
  * an office has exactly one CEO (also a unique index)
  * a hire's config must match its employee type's config_schema
  * high-risk types (Outreach Specialist) can only be hired after the owner accepts the outreach terms
  * a workflow only activates if the office has every employee type it needs (skip_if_missing
    states excepted); `allow_missing` records a deliberate exception and why
  * hires, fires, role changes and activations are written to audit_log
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..employees.registry import CatalogueError
from ..orchestration.workflow_validator import activation_problems
from .catalogue import Catalogue
from .clock import Clock, utcnow
from .context import OrgContext
from .models import AuditLog, Employee, Membership, Organization, OrgWorkflow, User
from .repo import NotFound, one_or_404, scoped

ROLES = {"owner", "ceo", "manager", "viewer"}


class OfficeError(ValueError):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "office"


def context_for(org: Organization, user_id: str | None = None, roles: set[str] | None = None,
                delegated: bool = False) -> OrgContext:
    return OrgContext(org_id=org.id, slug=org.slug, name=org.name, is_internal=org.is_internal, user_id=user_id,
                      roles=frozenset(roles or ()), approvals_delegated=delegated, settings=dict(org.settings or {}))


@dataclass
class Offices:
    sessions: sessionmaker[Session]
    catalogue: Catalogue
    clock: Clock = utcnow

    # ------------------------------------------------------------------ lookups

    def org(self, slug_or_id: str) -> Organization:
        with self.sessions() as s:
            org = s.scalars(select(Organization).where(
                (Organization.slug == slug_or_id) | (Organization.id == slug_or_id))).first()
        if not org:
            raise NotFound(f"No office '{slug_or_id}'")
        return org

    def orgs(self) -> list[Organization]:
        with self.sessions() as s:
            return list(s.scalars(select(Organization).order_by(Organization.created_at)))

    def system_ctx(self, slug_or_id: str) -> OrgContext:
        return context_for(self.org(slug_or_id))

    def user_ctx(self, user_id: str, slug_or_id: str) -> OrgContext:
        """The context for a logged-in user acting in an office they belong to."""
        org = self.org(slug_or_id)
        with self.sessions() as s:
            rows = list(s.scalars(select(Membership).where(Membership.org_id == org.id, Membership.user_id == user_id)))
        if not rows:
            raise PermissionError("You're not a member of this office")
        return context_for(org, user_id, {m.role for m in rows}, any(m.approvals_delegated for m in rows))

    def memberships(self, user_id: str) -> list[tuple[Organization, set[str]]]:
        with self.sessions() as s:
            rows = s.execute(select(Organization, Membership.role).join(Membership, Membership.org_id == Organization.id)
                             .where(Membership.user_id == user_id).order_by(Organization.created_at)
                             .execution_options(cross_org=True)).all()
        out: dict[str, tuple[Organization, set[str]]] = {}
        for org, role in rows:
            out.setdefault(org.id, (org, set()))[1].add(role)
        return list(out.values())

    def user(self, email: str) -> User | None:
        with self.sessions() as s:
            return s.scalars(select(User).where(User.email == email.strip().lower())).first()

    def ensure_user(self, email: str, name: str | None = None) -> User:
        email = email.strip().lower()
        with self.sessions.begin() as s:
            user = s.scalars(select(User).where(User.email == email)).first()
            if not user:
                user = User(email=email, name=name or email.split("@")[0])
                s.add(user)
                s.flush()
            return user

    # ------------------------------------------------------------------ offices

    def create_office(self, name: str, *, templates: list[str], ceo_email: str, owner_email: str | None = None,
                      slug: str | None = None, internal: bool = False, allow_missing: set[str] | None = None) -> OrgContext:
        """Create an office from templates: members, the recommended team (shared types hired once), workflows."""
        for key in templates:
            if key not in self.catalogue.templates:
                raise OfficeError(f"No office template '{key}' (have: {', '.join(self.catalogue.templates)})")
        slug = slug or slugify(name)
        with self.sessions.begin() as s:
            if s.scalars(select(Organization).where(Organization.slug == slug)).first():
                raise OfficeError(f"An office called '{slug}' already exists")
            org = Organization(name=name, slug=slug, plan_key="internal" if internal else None, is_internal=internal,
                               settings={})
            s.add(org)
            s.flush()
            org_id = org.id
        owner = self.ensure_user(owner_email or ceo_email)
        ceo = self.ensure_user(ceo_email)
        ctx = context_for(self.org(org_id))
        self.add_member(ctx, owner.id, "owner")
        self.add_member(ctx, ceo.id, "ceo")

        hired: set[tuple[str, str]] = set()
        skipped, waiting = [], set(allow_missing or ())
        for key in templates:
            for member in self.catalogue.templates[key].get("recommended_team", []):
                ident = (member["type"], member["name"])
                if ident in hired:
                    continue
                try:
                    self.hire(ctx, member["type"], member["name"], member.get("config"))
                    hired.add(ident)
                except OfficeError as e:
                    # e.g. a high-risk type before the outreach terms are accepted: the workflow runs
                    # without it and items wait at its states until it's hired
                    skipped.append(f"{member['name']}: {e}")
                    waiting.add(member["type"])
        for key in templates:
            for wf_key in self.catalogue.templates[key].get("workflows", []):
                self.activate_workflow(ctx, wf_key, allow_missing=waiting, reason="; ".join(skipped) or None)
        return self.system_ctx(org_id)

    def add_member(self, ctx: OrgContext, user_id: str, role: str, delegated: bool = False) -> None:
        if role not in ROLES:
            raise OfficeError(f"Role must be one of {', '.join(sorted(ROLES))}")
        with self.sessions.begin() as s:
            if role == "ceo" and s.scalars(scoped(Membership, ctx).where(Membership.role == "ceo")).first():
                raise OfficeError("This office already has a CEO")
            s.add(Membership(org_id=ctx.org_id, user_id=user_id, role=role, approvals_delegated=delegated))
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="member.add", target=user_id,
                           meta={"role": role}, ts=self.clock()))

    def accept_outreach_terms(self, ctx: OrgContext, postal_address: str) -> None:
        """The owner accepts the outreach terms and sets the postal address for email footers (spec v2 §11)."""
        if not postal_address.strip():
            raise OfficeError("A postal address for email footers is required")
        with self.sessions.begin() as s:
            org = s.scalars(select(Organization).where(Organization.id == ctx.org_id)).one()
            org.outreach_terms_accepted_at = self.clock()
            org.settings = {**(org.settings or {}), "postal_address": postal_address.strip()}
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="outreach_terms.accept",
                           meta={"postal_address": postal_address.strip()}, ts=self.clock()))

    # ------------------------------------------------------------------ team

    def employees(self, ctx: OrgContext, include_fired: bool = False) -> list[Employee]:
        with self.sessions() as s:
            query = scoped(Employee, ctx).order_by(Employee.name)
            if not include_fired:
                query = query.where(Employee.fired_at.is_(None))
            return list(s.scalars(query))

    def hire(self, ctx: OrgContext, type_key: str, name: str, config: dict | None = None) -> Employee:
        type_def = self.catalogue.types.get(type_key)
        if not type_def:
            raise OfficeError(f"No employee type '{type_key}'")
        if not type_def.hireable:
            raise OfficeError(f"{type_def.display_name} isn't available yet ({type_def.status})")
        try:
            validated = type_def.validate_config(config)
        except CatalogueError as e:
            raise OfficeError(str(e)) from e
        org = self.org(ctx.org_id)
        if type_def.risk_level == "high" and not org.outreach_terms_accepted_at:
            raise OfficeError(f"{type_def.display_name} needs the owner to accept the outreach terms first")
        with self.sessions.begin() as s:
            if s.scalars(scoped(Employee, ctx).where(Employee.name == name, Employee.fired_at.is_(None))).first():
                raise OfficeError(f"There's already an employee called {name}")
            emp = Employee(org_id=ctx.org_id, type_key=type_key, type_version=type_def.version, name=name,
                           config=validated, enabled=True, hired_at=self.clock())
            s.add(emp)
            s.flush()
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="employee.hire", target=emp.id,
                           meta={"type": type_key, "name": name, "config": validated}, ts=self.clock()))
            # Workflows that were running without this type no longer wait for it
            for ow in s.scalars(scoped(OrgWorkflow, ctx)):
                missing = list((ow.settings or {}).get("allow_missing", []))
                if type_key in missing:
                    missing.remove(type_key)
                    settings = {**ow.settings, "allow_missing": missing}
                    if not missing:
                        settings.pop("missing_reason", None)
                    ow.settings = settings
            return emp

    def fire(self, ctx: OrgContext, employee_id: str) -> None:
        with self.sessions.begin() as s:
            emp = one_or_404(s, Employee, ctx, id=employee_id)
            emp.fired_at = self.clock()
            emp.enabled = False
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="employee.fire", target=emp.id,
                           meta={"name": emp.name}, ts=self.clock()))

    def update_employee(self, ctx: OrgContext, employee_id: str, config: dict) -> Employee:
        with self.sessions.begin() as s:
            emp = one_or_404(s, Employee, ctx, id=employee_id)
            try:
                merged = {k: v for k, v in {**emp.config, **config}.items() if v is not None}  # None clears a field
                emp.config = self.catalogue.types[emp.type_key].validate_config(merged)
            except CatalogueError as e:
                raise OfficeError(str(e)) from e
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="employee.update", target=emp.id,
                           meta={"config": emp.config}, ts=self.clock()))
            return emp

    # ------------------------------------------------------------------ workflows

    def activation_problems(self, ctx: OrgContext, workflow_key: str, allow_missing: set[str] | None = None) -> list[str]:
        wf = self.catalogue.workflows[workflow_key]
        hired = {e.type_key for e in self.employees(ctx) if e.enabled}
        return activation_problems(wf, hired, self.catalogue.type_names(), allow_missing or set())

    def activate_workflow(self, ctx: OrgContext, workflow_key: str, allow_missing: set[str] | None = None,
                          reason: str | None = None) -> None:
        if workflow_key not in self.catalogue.workflows:
            raise OfficeError(f"No workflow '{workflow_key}'")
        wf = self.catalogue.workflows[workflow_key]
        allow = set(allow_missing or ())
        problems = self.activation_problems(ctx, workflow_key, allow)
        if problems:
            raise OfficeError(" ".join(problems))
        missing = sorted(t for t in allow if t not in {e.type_key for e in self.employees(ctx)})
        settings = {"allow_missing": missing}
        if missing and reason:
            settings["missing_reason"] = reason
        with self.sessions.begin() as s:
            row = s.scalars(scoped(OrgWorkflow, ctx).where(OrgWorkflow.workflow_key == workflow_key)).first()
            if row:
                row.active, row.settings, row.workflow_version = True, settings, wf.version
            else:
                s.add(OrgWorkflow(org_id=ctx.org_id, workflow_key=workflow_key, workflow_version=wf.version,
                                  active=True, settings=settings))
            s.add(AuditLog(org_id=ctx.org_id, user_id=ctx.user_id, action="workflow.activate", target=workflow_key,
                           meta=settings, ts=self.clock()))

    def workflows(self, ctx: OrgContext) -> list[OrgWorkflow]:
        with self.sessions() as s:
            return list(s.scalars(scoped(OrgWorkflow, ctx).order_by(OrgWorkflow.workflow_key)))
