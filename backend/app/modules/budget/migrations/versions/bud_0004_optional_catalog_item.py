"""budget — catalog_item_id becomes optional on budget_items, add free-text description.

Some clinics price treatments case-by-case instead of from a fixed
catalog list (see issue: manual pricing without a catalog item). A
budget line item can now exist without a ``catalog_item_id`` as long
as it carries its own ``description`` and ``unit_price`` snapshot.

Revision ID: bud_0004
Revises: bud_0003
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bud_0004"
down_revision: str | None = "bud_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "budget_items",
        sa.Column("description", sa.String(length=300), nullable=True),
    )
    op.alter_column(
        "budget_items",
        "catalog_item_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "budget_items",
        "catalog_item_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.drop_column("budget_items", "description")
