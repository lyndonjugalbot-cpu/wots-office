"""v2 multi-tenant core (spec v2 §15): tenancy, employees, workflows, work items, metering.

Moves the v1 tables aside, creates the v2 schema, then migrates the data:
  * creates the internal office "Wots Office" (slug wots-office) with its owner as owner + CEO
    (email from WOTS_OWNER_EMAIL, name from WOTS_OWNER_NAME)
  * hires the office-template team as employees (Echo, the Outreach Specialist, is left out: it
    can't be hired until the owner accepts the outreach terms, spec v2 §11)
  * leads -> work_items + lead_profiles (scope -> workflow_key, NEW items already claimed by
    Ledger -> VERIFY), llm_usage -> usage_events, org_id on every row, UUID ids
  * moves lead folders data/leads/{id}/ -> data/orgs/{org_id}/items/{id}/ when the app passes
    its data folder (wots.core.db.upgrade does; plain `alembic upgrade` skips the file move)

Revision ID: 0002
Revises: 0001
"""
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

V1_INDEXES = {
    "leads": ["ix_leads_assigned_to", "ix_leads_scope_status"],
    "llm_usage": ["ix_llm_usage_ts"],
    "suppression": ["ix_suppression_domain", "ix_suppression_email"],
    "ad_captures": ["ix_ad_captures_lead_id"],
    "approvals": ["ix_approvals_lead_id"],
    "artifacts": ["ix_artifacts_lead_id"],
    "events": ["ix_events_lead_id", "ix_events_ts"],
    "outreach": ["ix_outreach_lead_id"],
    "qa_reports": ["ix_qa_reports_lead_id"],
}

# Frozen copy of the web_agency + ad_agency teams at the time of this migration (shared types hired once)
TEAM = [
    ("Scout", "lead_researcher", {}),
    ("Ledger", "data_verifier", {}),
    ("Quill", "copywriter", {}),
    ("Iris", "graphic_designer", {"style_profile": "flat_brand"}),
    ("Pixel", "web_developer", {"style_profile": "clean_modern"}),
    ("Nova", "web_developer", {"style_profile": "bold_warm"}),
    ("Hawk", "qa_tester", {}),
    ("Dock", "deployment", {}),
    ("Scout-Ads", "ad_researcher", {}),
    ("Lens", "creative_strategist", {}),
    ("Juno", "graphic_designer", {"style_profile": "bold_playful"}),
]
V1_AGENT_NAMES = {"scout": "Scout", "ledger": "Ledger", "quill": "Quill", "iris": "Iris", "pixel": "Pixel",
                  "nova": "Nova", "hawk": "Hawk", "dock": "Dock", "scout_ads": "Scout-Ads", "lens": "Lens",
                  "juno": "Juno"}
CREDIT_VALUE_USD = 0.01  # config/billing.yaml at the time of this migration


def upgrade() -> None:
    for table, indexes in V1_INDEXES.items():
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.rename_table(table, f"v1_{table}")

    op.create_table('employee_types',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('impl_path', sa.String(length=255), nullable=False),
    sa.Column('task_kinds', sa.JSON(), nullable=False),
    sa.Column('default_model', sa.String(length=60), nullable=False),
    sa.Column('config_schema', sa.JSON(), nullable=False),
    sa.Column('risk_level', sa.String(length=10), nullable=False),
    sa.Column('plans', sa.JSON(), nullable=False),
    sa.Column('est_credits_per_task', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.PrimaryKeyConstraint('key', 'version')
    )
    op.create_table('office_templates',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key', 'version')
    )
    op.create_table('organizations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('plan_key', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('outreach_terms_accepted_at', sa.DateTime(), nullable=True),
    sa.Column('is_internal', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('plans',
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('price_monthly', sa.Float(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('included_credits', sa.Integer(), nullable=False),
    sa.Column('max_employees', sa.Integer(), nullable=False),
    sa.Column('max_workflows', sa.Integer(), nullable=False),
    sa.Column('allowed_employee_types', sa.JSON(), nullable=False),
    sa.Column('features', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('auth_provider_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('workflow_defs',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key', 'version')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('target', sa.String(length=255), nullable=True),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_org_id'), ['org_id'], unique=False)

    op.create_table('credit_ledger',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('delta', sa.Float(), nullable=False),
    sa.Column('reason', sa.String(length=20), nullable=False),
    sa.Column('ref', sa.String(length=255), nullable=True),
    sa.Column('balance_after', sa.Float(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('credit_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_ledger_org_id'), ['org_id'], unique=False)

    op.create_table('employees',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('type_key', sa.String(length=60), nullable=False),
    sa.Column('type_version', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('avatar', sa.String(length=255), nullable=True),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('hired_at', sa.DateTime(), nullable=False),
    sa.Column('fired_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_employees_org_id'), ['org_id'], unique=False)

    op.create_table('integrations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('secret_encrypted', sa.Text(), nullable=True),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('integrations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integrations_org_id'), ['org_id'], unique=False)

    op.create_table('memberships',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('approvals_delegated', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'user_id', 'role', name='uq_membership_role')
    )
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memberships_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memberships_user_id'), ['user_id'], unique=False)
        batch_op.create_index('uq_one_ceo_per_org', ['org_id'], unique=True, sqlite_where=sa.text("role = 'ceo'"), postgresql_where=sa.text("role = 'ceo'"))

    op.create_table('org_workflows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('workflow_key', sa.String(length=60), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'workflow_key', name='uq_org_workflow')
    )
    with op.batch_alter_table('org_workflows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_workflows_org_id'), ['org_id'], unique=False)

    op.create_table('subscriptions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('stripe_customer_id', sa.String(length=255), nullable=True),
    sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True),
    sa.Column('plan_key', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('current_period_end', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscriptions_org_id'), ['org_id'], unique=False)

    op.create_table('suppression',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('suppression', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_suppression_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_suppression_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_suppression_org_id'), ['org_id'], unique=False)

    op.create_table('work_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('workflow_key', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('assigned_employee_id', sa.String(length=36), nullable=True),
    sa.Column('fix_count', sa.Integer(), nullable=False),
    sa.Column('claimed_by', sa.String(length=80), nullable=True),
    sa.Column('claim_expires_at', sa.DateTime(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('disqualify_reason', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_work_items_assigned_employee_id'), ['assigned_employee_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_work_items_org_id'), ['org_id'], unique=False)
        batch_op.create_index('ix_work_items_org_workflow_status', ['org_id', 'workflow_key', 'status'], unique=False)

    op.create_table('ad_captures',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('platform', sa.String(length=32), nullable=False),
    sa.Column('ad_library_url', sa.String(length=500), nullable=True),
    sa.Column('screenshot_paths', sa.JSON(), nullable=False),
    sa.Column('ad_copy', sa.Text(), nullable=True),
    sa.Column('first_seen', sa.DateTime(), nullable=True),
    sa.Column('checklist', sa.JSON(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ad_captures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ad_captures_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ad_captures_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('approvals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('decided_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approvals_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_approvals_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('path', sa.String(length=500), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by_employee_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_artifacts_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_artifacts_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=True),
    sa.Column('from_status', sa.String(length=32), nullable=True),
    sa.Column('to_status', sa.String(length=32), nullable=True),
    sa.Column('actor_kind', sa.String(length=10), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_seq'), ['seq'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_ts'), ['ts'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('employee_id', sa.String(length=36), nullable=False),
    sa.Column('task', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('lead_profiles',
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('business_name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=120), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('region', sa.String(length=120), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('phone', sa.String(length=64), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('contact_name', sa.String(length=255), nullable=True),
    sa.Column('website_found', sa.String(length=500), nullable=True),
    sa.Column('social_links', sa.JSON(), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=True),
    sa.Column('registry_id', sa.String(length=64), nullable=True),
    sa.Column('source', sa.String(length=64), nullable=True),
    sa.Column('source_ref', sa.String(length=500), nullable=True),
    sa.Column('preview_url', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('work_item_id')
    )
    with op.batch_alter_table('lead_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lead_profiles_org_id'), ['org_id'], unique=False)

    op.create_table('outreach',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('provider_message_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outreach', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outreach_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_outreach_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('qa_reports',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('work_item_id', sa.String(length=36), nullable=False),
    sa.Column('artifact_version', sa.Integer(), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('issues', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('qa_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_qa_reports_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_qa_reports_work_item_id'), ['work_item_id'], unique=False)

    op.create_table('usage_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('employee_id', sa.String(length=36), nullable=True),
    sa.Column('work_item_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('credits', sa.Float(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('usage_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_events_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_usage_events_ts'), ['ts'], unique=False)




    _migrate_data(op.get_bind())
    # Tables that reference v1_leads go first, or SQLite's foreign key check refuses the drop
    for table in sorted(V1_INDEXES, key=lambda t: t == "leads"):
        op.drop_table(f"v1_{table}")


def downgrade() -> None:
    raise NotImplementedError("v2 -> v1 isn't supported. Restore the pre-migration backup of data/wots.db instead.")


def _new() -> str:
    return str(uuid.uuid4())


def _rows(conn, table):
    return [dict(r._mapping) for r in conn.execute(sa.text(f"SELECT * FROM {table}"))]


def _insert(conn, table, row):
    row = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
    cols = ", ".join(row)
    conn.execute(sa.text(f"INSERT INTO {table} ({cols}) VALUES ({', '.join(':' + c for c in row)})"), row)


def _migrate_data(conn) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stamp = {"created_at": now, "updated_at": now}

    # ---- the internal office and its owner (owner + CEO)
    org = _new()
    _insert(conn, "organizations", {"id": org, "name": "Wots Office", "slug": "wots-office", "plan_key": "internal",
                                    "status": "active", "settings": {}, "is_internal": True, **stamp})
    owner = _new()
    _insert(conn, "users", {"id": owner, "email": os.environ.get("WOTS_OWNER_EMAIL") or "owner@wots-office.local",
                            "name": os.environ.get("WOTS_OWNER_NAME") or "Lyndon", **stamp})
    for role in ("owner", "ceo"):
        _insert(conn, "memberships", {"id": _new(), "org_id": org, "user_id": owner, "role": role,
                                      "approvals_delegated": False, **stamp})

    # ---- employees hired from the office templates
    employees = {}
    for name, type_key, config in TEAM:
        employees[name] = _new()
        _insert(conn, "employees", {"id": employees[name], "org_id": org, "type_key": type_key, "type_version": 1,
                                    "name": name, "config": config, "enabled": True, "hired_at": now, **stamp})
    by_v1_name = {k: employees[v] for k, v in V1_AGENT_NAMES.items()}
    pending = {"allow_missing": ["cold_email"],
               "missing_reason": "Outreach Specialist can't be hired until the owner accepts the outreach terms"}
    for key in ("website", "ad_refresh"):
        _insert(conn, "org_workflows", {"id": _new(), "org_id": org, "workflow_key": key, "workflow_version": 1,
                                        "active": True, "settings": pending, **stamp})

    # ---- leads -> work items + lead profiles
    items = {}
    for lead in _rows(conn, "v1_leads"):
        items[lead["id"]] = item = _new()
        status = "VERIFY" if lead["status"] == "NEW" and lead["claimed_by"] else lead["status"]
        _insert(conn, "work_items", {
            "id": item, "org_id": org, "workflow_key": lead["scope"], "kind": "lead", "status": status,
            "assigned_employee_id": by_v1_name.get(lead["assigned_to"] or ""), "fix_count": lead["fix_count"] or 0,
            "claimed_by": None, "claim_expires_at": None, "priority": 0, "disqualify_reason": lead["disqualify_reason"],
            "created_at": lead["created_at"], "updated_at": lead["updated_at"]})
        profile = {k: lead[k] for k in ("business_name", "category", "description", "country", "region", "timezone",
                                        "address", "phone", "email", "contact_name", "website_found", "social_links",
                                        "entity_type", "registry_id", "source", "source_ref", "preview_url")}
        if isinstance(profile["social_links"], str):
            profile["social_links"] = json.loads(profile["social_links"] or "{}")
        _insert(conn, "lead_profiles", {"work_item_id": item, "org_id": org, **profile,
                                        "created_at": lead["created_at"], "updated_at": lead["updated_at"]})

    def item_of(row):
        return items.get(row["lead_id"])

    for a in _rows(conn, "v1_artifacts"):
        if item_of(a):
            _insert(conn, "artifacts", {"id": _new(), "org_id": org, "work_item_id": item_of(a), "kind": a["kind"],
                                        "path": a["path"], "version": a["version"],
                                        "created_by_employee_id": by_v1_name.get(a["created_by"]),
                                        "created_at": a["created_at"], "updated_at": a["created_at"]})
    for q in _rows(conn, "v1_qa_reports"):
        if item_of(q):
            issues = json.loads(q["issues"]) if isinstance(q["issues"], str) else q["issues"]
            _insert(conn, "qa_reports", {"id": _new(), "org_id": org, "work_item_id": item_of(q),
                                         "artifact_version": q["artifact_version"], "passed": q["passed"],
                                         "issues": issues, "created_at": q["created_at"], "updated_at": q["created_at"]})
    for a in _rows(conn, "v1_approvals"):
        if item_of(a):
            _insert(conn, "approvals", {"id": _new(), "org_id": org, "work_item_id": item_of(a), "kind": a["kind"],
                                        "decision": a["decision"], "notes": a["notes"], "decided_by_user_id": owner,
                                        "decided_at": a["decided_at"], **stamp})
    for o in _rows(conn, "v1_outreach"):
        if item_of(o):
            _insert(conn, "outreach", {"id": _new(), "org_id": org, "work_item_id": item_of(o), "kind": o["kind"],
                                       "subject": o["subject"], "body": o["body"], "scheduled_for": o["scheduled_for"],
                                       "sent_at": o["sent_at"], "status": o["status"],
                                       "provider_message_id": o["provider_message_id"], **stamp})
    for c in _rows(conn, "v1_ad_captures"):
        if item_of(c):
            _insert(conn, "ad_captures", {"id": _new(), "org_id": org, "work_item_id": item_of(c),
                                          "platform": c["platform"], "ad_library_url": c["ad_library_url"],
                                          "screenshot_paths": json.loads(c["screenshot_paths"] or "[]"),
                                          "ad_copy": c["ad_copy"], "first_seen": c["first_seen"],
                                          "checklist": json.loads(c["checklist"] or "{}"), "notes": c["notes"], **stamp})
    for sup in _rows(conn, "v1_suppression"):
        _insert(conn, "suppression", {"id": _new(), "org_id": org, "email": sup["email"], "domain": sup["domain"],
                                      "reason": sup["reason"], "added_at": sup["added_at"], **stamp})
    for seq, e in enumerate(sorted(_rows(conn, "v1_events"), key=lambda r: r["id"]), start=1):
        actor = e["actor"]
        if actor == "ceo":
            kind, actor_id = "user", owner
        elif actor in by_v1_name:
            kind, actor_id = "employee", by_v1_name[actor]
        else:
            kind, actor_id = "system", actor  # atlas, import, ...
        _insert(conn, "events", {"id": _new(), "seq": seq, "org_id": org, "work_item_id": items.get(e["lead_id"]),
                                 "from_status": e["from_status"], "to_status": e["to_status"], "actor_kind": kind,
                                 "actor_id": actor_id, "note": e["note"], "ts": e["ts"]})
    for u in _rows(conn, "v1_llm_usage"):
        _insert(conn, "usage_events", {"id": _new(), "org_id": org, "employee_id": by_v1_name.get(u["agent"]),
                                       "work_item_id": items.get(u["lead_id"]), "kind": "llm", "model": u["model"],
                                       "input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
                                       "cost_usd": u["cost_usd"], "credits": round(u["cost_usd"] / CREDIT_VALUE_USD, 4),
                                       "ts": u["ts"]})

    # ---- files: data/leads/{id}/ -> data/orgs/{org_id}/items/{uuid}/
    data_dir = context.config.attributes.get("data_dir")
    if data_dir and items:
        for old_id, item in items.items():
            src = Path(data_dir) / "leads" / str(old_id)
            if src.is_dir():
                dest = Path(data_dir) / "orgs" / org / "items" / item
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
        leads_dir = Path(data_dir) / "leads"
        if leads_dir.is_dir() and not any(leads_dir.iterdir()):
            leads_dir.rmdir()
