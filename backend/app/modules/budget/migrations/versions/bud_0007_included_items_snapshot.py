"""budget — price-free item list for manual-total budgets.

A manual-total budget (ADR 0018) carries no ``BudgetItem`` rows, so the
patient had no way to read what the quote covers — only the figure.
``included_items_snapshot`` holds the treatment list *without prices*:
localized names plus tooth/surfaces, copied from the catalog at creation
time so the document survives a later catalog rename.

Nullable and unpopulated for existing rows: itemized budgets keep
rendering from ``budget_items`` and never read this column.

Revision ID: bud_0007
Revises: bud_0006
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bud_0007"
down_revision: str | None = "bud_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("included_items_snapshot", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budgets", "included_items_snapshot")
