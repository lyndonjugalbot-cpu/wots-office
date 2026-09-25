"""Phase 2: postcode (for dedupe) and verification results on lead profiles.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lead_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("postcode", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("checks", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.create_index(batch_op.f("ix_lead_profiles_postcode"), ["postcode"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("lead_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lead_profiles_postcode"))
        batch_op.drop_column("checks")
        batch_op.drop_column("postcode")
