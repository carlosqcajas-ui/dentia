"""budget — add is_manual_total flag for free-total budgets.

Clinics without a fiscal invoicing integration (e.g. no ``billing``
usage) don't need a line-item breakdown on their quotes — they just
need a total they can set and edit by hand, at any status, including
after the patient has signed. ``is_manual_total`` opts a budget into
that mode; itemized budgets (the default) are unaffected.

Revision ID: bud_0005
Revises: bud_0004
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bud_0005"
down_revision: str | None = "bud_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column(
            "is_manual_total", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("budgets", "is_manual_total")
