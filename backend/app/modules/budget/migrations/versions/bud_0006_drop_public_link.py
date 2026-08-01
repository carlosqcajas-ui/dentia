"""budget — drop the patient-facing public link (2FA, signature over
the wire, access-log/lockout).

The clinic decided budgets should never be accepted/rejected by the
patient directly (no fiscal/legal need for a self-service remote
channel at this deployment's scale). Staff-side acceptance
(``/accept``, ``/reject``, ``/accept-in-clinic``) is unaffected —
those don't touch any of the columns/table dropped here. See the ADR
that supersedes ``0006-budget-public-link-2-factor-auth``.

Drops:

- ``budget_access_logs`` table (verification attempt audit/lockout).
- Public-link fields on ``budgets``: ``public_token``, ``viewed_at``,
  ``last_reminder_sent_at``, ``public_auth_method``,
  ``public_auth_secret_hash``, ``public_locked_at``.

Keeps ``accepted_via`` / ``rejection_reason`` / ``rejection_note`` —
still used by the staff-facing accept/reject flow.

Revision ID: bud_0006
Revises: bud_0005
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bud_0006"
down_revision: str | None = "bud_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "idx_budget_access_logs_budget_attempted",
        table_name="budget_access_logs",
    )
    op.drop_table("budget_access_logs")

    op.drop_index("idx_budgets_public_token", table_name="budgets")
    op.drop_column("budgets", "public_locked_at")
    op.drop_column("budgets", "public_auth_secret_hash")
    op.drop_column("budgets", "public_auth_method")
    op.drop_column("budgets", "last_reminder_sent_at")
    op.drop_column("budgets", "viewed_at")
    op.drop_column("budgets", "public_token")


def downgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("public_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("last_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("public_auth_method", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("public_auth_secret_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("public_locked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("UPDATE budgets SET public_token = gen_random_uuid() WHERE public_token IS NULL")
    op.execute(
        "UPDATE budgets SET public_auth_method = 'none' WHERE public_auth_method IS NULL"
    )
    op.alter_column("budgets", "public_token", nullable=False)
    op.alter_column("budgets", "public_auth_method", nullable=False)
    op.create_index(
        "idx_budgets_public_token",
        "budgets",
        ["public_token"],
        unique=True,
    )

    op.create_table(
        "budget_access_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "budget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("budgets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("method_attempted", sa.String(length=20), nullable=False),
    )
    op.create_index(
        "idx_budget_access_logs_budget_attempted",
        "budget_access_logs",
        ["budget_id", "attempted_at"],
    )
