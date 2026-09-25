"""Database tables (spec v2 §9). Portable SQLAlchemy: SQLite locally, Postgres from Phase 7.

Every tenant table has an indexed `org_id`, and all access goes through the org-scoped
repository in repo.py. Ids are UUID strings.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .clock import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class _Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def _org_fk() -> Mapped[str]:
    return mapped_column(String(36), ForeignKey("organizations.id"), index=True)


# ---------------------------------------------------------------- tenancy & people

class Organization(_Timestamps, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    plan_key: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | cancelled
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    outreach_terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)


class User(_Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str | None] = mapped_column(String(120))
    auth_provider_id: Mapped[str | None] = mapped_column(String(255))


class Membership(_Timestamps, Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # owner | ceo | manager | viewer
    approvals_delegated: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "role", name="uq_membership_role"),
        # Exactly one CEO per office
        Index("uq_one_ceo_per_org", "org_id", unique=True,
              sqlite_where=text("role = 'ceo'"), postgresql_where=text("role = 'ceo'")),
    )


# ---------------------------------------------------------------- catalogue (platform-level, no org_id)

class EmployeeType(Base):
    __tablename__ = "employee_types"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text)
    impl_path: Mapped[str] = mapped_column(String(255))
    task_kinds: Mapped[list] = mapped_column(JSON, default=list)
    default_model: Mapped[str] = mapped_column(String(60))
    config_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(10))
    plans: Mapped[list] = mapped_column(JSON, default=list)
    est_credits_per_task: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))


class WorkflowDef(Base):
    __tablename__ = "workflow_defs"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON)


class OfficeTemplate(Base):
    __tablename__ = "office_templates"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON)


class Plan(Base):
    __tablename__ = "plans"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    price_monthly: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    included_credits: Mapped[int] = mapped_column(Integer, default=0)
    max_employees: Mapped[int] = mapped_column(Integer, default=0)
    max_workflows: Mapped[int] = mapped_column(Integer, default=0)
    allowed_employee_types: Mapped[list] = mapped_column(JSON, default=list)
    features: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- office setup

class Employee(_Timestamps, Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    type_key: Mapped[str] = mapped_column(String(60))
    type_version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    avatar: Mapped[str | None] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime)


class OrgWorkflow(_Timestamps, Base):
    __tablename__ = "org_workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    workflow_key: Mapped[str] = mapped_column(String(60))
    workflow_version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("org_id", "workflow_key", name="uq_org_workflow"),)


class Integration(_Timestamps, Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    kind: Mapped[str] = mapped_column(String(30))  # email|vercel|places|companies_house|abn|webhook
    status: Mapped[str] = mapped_column(String(20), default="connected")
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- work

class WorkItem(_Timestamps, Base):
    __tablename__ = "work_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    workflow_key: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20), default="lead")
    status: Mapped[str] = mapped_column(String(32))
    assigned_employee_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("employees.id"), index=True)
    fix_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(80))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    disqualify_reason: Mapped[str | None] = mapped_column(String(255))
    __table_args__ = (Index("ix_work_items_org_workflow_status", "org_id", "workflow_key", "status"),)


class LeadProfile(_Timestamps, Base):
    __tablename__ = "lead_profiles"
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), primary_key=True)
    org_id: Mapped[str] = _org_fk()
    business_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    postcode: Mapped[str | None] = mapped_column(String(16), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    website_found: Mapped[str | None] = mapped_column(String(500))
    social_links: Mapped[dict] = mapped_column(JSON, default=dict)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    registry_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(500))
    preview_url: Mapped[str | None] = mapped_column(String(500))
    checks: Mapped[dict] = mapped_column(JSON, default=dict)  # what verification found, and from where


class AdCapture(_Timestamps, Base):
    __tablename__ = "ad_captures"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    ad_library_url: Mapped[str | None] = mapped_column(String(500))
    screenshot_paths: Mapped[list] = mapped_column(JSON, default=list)
    ad_copy: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    checklist: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)


class Artifact(_Timestamps, Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # copy|brief|logo|hero|site|ad_variant|qa_report|pitch
    path: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_employee_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("employees.id"))


class QAReport(_Timestamps, Base):
    __tablename__ = "qa_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    artifact_version: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list] = mapped_column(JSON, default=list)


class Approval(_Timestamps, Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # build|pitch|escalation
    decision: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Outreach(_Timestamps, Base):
    __tablename__ = "outreach"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # initial|followup
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))


class Suppression(_Timestamps, Base):
    __tablename__ = "suppression"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    reason: Mapped[str] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(Integer, index=True)  # strictly increasing; UUIDs don't sort
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    actor_kind: Mapped[str] = mapped_column(String(10))  # employee | user | system
    actor_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    work_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_items.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), ForeignKey("employees.id"))
    task: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- metering & billing

class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    employee_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("employees.id"))
    work_item_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("work_items.id"))
    kind: Mapped[str] = mapped_column(String(20))  # llm|places_call|render|deploy|email_send
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    credits: Mapped[float] = mapped_column(Float, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(20))  # plan_grant|topup|usage|adjustment
    ref: Mapped[str | None] = mapped_column(String(255))
    balance_after: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subscription(_Timestamps, Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    plan_key: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = _org_fk()
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# Tables that belong to one office: every query on them must filter by org_id (see repo.py)
TENANT_TABLES = frozenset(
    t.name for t in Base.metadata.sorted_tables if "org_id" in t.columns
)
