"""core — regionalize clinic defaults to Bolivia (La Paz).

This deployment is committed to a single-country rollout: Bolivia. The
timezone/currency defaults were Spain-oriented (``Europe/Madrid`` /
``EUR``) from the project's origin. This migration flips the column
defaults for any clinic created from here on, and backfills existing
clinics that are still sitting on the old defaults (safe because
nothing in this deployment had a legitimate reason to be on EUR /
Europe/Madrid).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("clinics", "timezone", server_default="America/La_Paz")
    op.alter_column("clinics", "currency", server_default="BOB")
    op.execute(
        "UPDATE clinics SET timezone = 'America/La_Paz' WHERE timezone = 'Europe/Madrid';"
    )
    op.execute("UPDATE clinics SET currency = 'BOB' WHERE currency = 'EUR';")


def downgrade() -> None:
    op.alter_column("clinics", "timezone", server_default="Europe/Madrid")
    op.alter_column("clinics", "currency", server_default="EUR")
