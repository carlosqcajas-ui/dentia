"""payments — sequential receipt number + per-clinic counter.

Adds ``payments.receipt_number`` (sequential per clinic) and the
``payment_receipt_counters`` table that hands the numbers out under a
row lock. Backs existing rows in chronological order per clinic so the
numbering reads correctly from day one, then seeds each clinic's counter
past its highest backfilled number.

The column stays NULLable: making it NOT NULL would need every future
write path to be aware of it before the constraint could land, and the
uniqueness backstop below already prevents the failure that matters
(two receipts sharing a number). NULL simply means "recorded before this
migration and outside any clinic" — the backfill leaves none behind.

Revision ID: pay_0004
Revises: pay_0003
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "pay_0004"
down_revision: str | None = "pay_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("receipt_number", sa.Integer(), nullable=True))

    op.create_table(
        "payment_receipt_counters",
        sa.Column("clinic_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("current_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"]),
        sa.PrimaryKeyConstraint("clinic_id"),
        sa.CheckConstraint("current_number >= 0", name="ck_receipt_counter_non_negative"),
    )

    # Backfill: number every existing payment per clinic, oldest first.
    # ``payment_date`` is the business date; ``created_at`` breaks ties
    # deterministically and ``id`` is the final tie-breaker so repeated
    # runs on a restored dump produce identical numbering.
    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY clinic_id
                       ORDER BY payment_date, created_at, id
                   ) AS seq
              FROM payments
        )
        UPDATE payments p
           SET receipt_number = numbered.seq
          FROM numbered
         WHERE p.id = numbered.id
        """
    )

    # Seed one counter row per clinic that already has payments, parked
    # at the highest number handed out above.
    op.execute(
        """
        INSERT INTO payment_receipt_counters (clinic_id, current_number)
        SELECT clinic_id, max(receipt_number)
          FROM payments
         GROUP BY clinic_id
        """
    )

    op.create_unique_constraint(
        "uq_payments_clinic_receipt_number", "payments", ["clinic_id", "receipt_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_payments_clinic_receipt_number", "payments", type_="unique")
    op.drop_table("payment_receipt_counters")
    op.drop_column("payments", "receipt_number")
