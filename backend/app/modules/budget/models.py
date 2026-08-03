"""Budget module database models."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.core.auth.models import Clinic, User
    from app.modules.catalog.models import TreatmentCatalogItem, VatType
    from app.modules.odontogram.models import Treatment
    from app.modules.patients.models import Patient


class Budget(Base, TimestampMixin):
    """Main budget entity for dental treatment quotes.

    Supports versioning, partial acceptance, and integration with
    catalog, odontogram, and future billing modules.
    """

    __tablename__ = "budgets"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), index=True)

    # Identification
    budget_number: Mapped[str] = mapped_column(String(50))  # e.g., "PRES-2024-0001"
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_budget_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("budgets.id"), index=True, default=None
    )  # For version chain

    # Status workflow (simplified)
    status: Mapped[str] = mapped_column(
        String(30), default="draft"
    )  # draft, accepted, completed, rejected, expired, cancelled

    # Validity period
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date, default=None)

    # Assignments
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    assigned_professional_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )

    # Global discount
    global_discount_type: Mapped[str | None] = mapped_column(
        String(20), default=None
    )  # percentage, absolute
    global_discount_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)

    # Totals (calculated from items, unless is_manual_total)
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )  # Sum of line totals before global discount
    total_discount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )  # Total discounts (line + global)
    total_tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))  # Total VAT
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))  # Final amount

    # When True, ``total`` is set directly by staff via
    # ``BudgetService.set_manual_total`` instead of being derived from
    # ``items`` — for clinics that don't need a line-item breakdown
    # (e.g. no fiscal invoicing integration). Editable in any status,
    # including after acceptance — see CLAUDE.md gotcha on tamper-evidence.
    is_manual_total: Mapped[bool] = mapped_column(Boolean, default=False)

    # Notes
    internal_notes: Mapped[str | None] = mapped_column(Text, default=None)
    patient_notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Future integrations
    insurance_estimate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), default=None
    )  # Reserved for insurance module

    # Acceptance / rejection metadata --------------------------------------
    # ``in_clinic`` (reception captured the acceptance, optionally with a
    # tablet signature) | ``manual`` (legacy records).
    accepted_via: Mapped[str | None] = mapped_column(String(20), default=None)
    # Free-form reason/note captured by staff when recording a rejection.
    rejection_reason: Mapped[str | None] = mapped_column(String(50), default=None)
    rejection_note: Mapped[str | None] = mapped_column(Text, default=None)

    # Plan snapshots ------------------------------------------------------
    # Read-only denormalized fields populated when the budget is created
    # from a treatment plan. Let endpoints that live in the budget module
    # render plan context without importing ``treatment_plan`` models.
    # Real-time plan state lives in the treatment_plan module — query
    # that module for live status. See ADR 0003.
    plan_number_snapshot: Mapped[str | None] = mapped_column(String(50), default=None)
    plan_status_snapshot: Mapped[str | None] = mapped_column(String(20), default=None)

    # What a manual-total budget covers, WITHOUT prices. A manual-total
    # budget has no ``BudgetItem`` rows by definition (ADR 0018), but the
    # patient still needs to read what is being quoted. Shape:
    #
    #     [{"names": {"es": "Inlay", "en": "Inlay"},
    #       "tooth_number": 26, "surfaces": ["M", "O"]}, ...]
    #
    # Localized names are copied from the catalog at creation time so the
    # document survives a catalog rename or deletion — same reasoning as
    # ``plan_number_snapshot`` above. Never stores an amount: the whole
    # point of this mode is that only the professional's total is shown.
    included_items_snapshot: Mapped[list | None] = mapped_column(JSONB, default=None)

    # Soft delete
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    clinic: Mapped["Clinic"] = relationship(foreign_keys=[clinic_id])
    patient: Mapped["Patient"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    assigned_professional: Mapped["User | None"] = relationship(
        foreign_keys=[assigned_professional_id]
    )
    parent_budget: Mapped["Budget | None"] = relationship(
        remote_side="Budget.id", foreign_keys=[parent_budget_id]
    )
    items: Mapped[list["BudgetItem"]] = relationship(
        back_populates="budget", cascade="all, delete-orphan", order_by="BudgetItem.display_order"
    )
    signatures: Mapped[list["BudgetSignature"]] = relationship(
        back_populates="budget", cascade="all, delete-orphan"
    )
    history: Mapped[list["BudgetHistory"]] = relationship(
        back_populates="budget",
        cascade="all, delete-orphan",
        order_by="BudgetHistory.changed_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint(
            "clinic_id", "budget_number", "version", name="uq_budget_clinic_number_version"
        ),
        Index("idx_budgets_clinic", "clinic_id"),
        Index("idx_budgets_clinic_patient", "clinic_id", "patient_id"),
        Index("idx_budgets_clinic_status", "clinic_id", "status"),
        Index("idx_budgets_valid_until", "valid_until"),
        Index("idx_budgets_parent", "parent_budget_id"),
    )


class BudgetItem(Base, TimestampMixin):
    """Individual line item in a budget.

    References catalog items with snapshotted prices at time of creation.
    Supports per-item acceptance and treatment tracking.
    """

    __tablename__ = "budget_items"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), index=True)
    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), index=True
    )

    # Catalog reference — optional. Clinics that price case-by-case can
    # add a line item with just ``description`` + ``unit_price`` and no
    # catalog backing.
    catalog_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("treatment_catalog_items.id"), index=True, default=None
    )

    # Free-text label, required when there's no catalog_item to name the line.
    description: Mapped[str | None] = mapped_column(String(300), default=None)

    # Snapshotted pricing (frozen at time of creation)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    # Line discount
    discount_type: Mapped[str | None] = mapped_column(
        String(20), default=None
    )  # percentage, absolute
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)

    # VAT (snapshotted)
    vat_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vat_types.id"), index=True, default=None
    )
    vat_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Calculated line totals
    line_subtotal: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00")
    )  # unit_price * quantity
    line_discount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00")
    )  # Applied discount
    line_tax: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))  # VAT amount
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00")
    )  # Final line amount

    # Dental specifics
    tooth_number: Mapped[int | None] = mapped_column(Integer, default=None)  # FDI notation
    surfaces: Mapped[list | None] = mapped_column(JSONB, default=None)  # ["M", "O", "D"]

    # Odontogram integration - link to a Treatment (header + teeth).
    treatment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("treatments.id"), index=True, default=None
    )

    # Billing tracking (for partial invoicing)
    invoiced_quantity: Mapped[int] = mapped_column(Integer, default=0)

    # Display
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Relationships
    clinic: Mapped["Clinic"] = relationship()
    budget: Mapped["Budget"] = relationship(back_populates="items")
    catalog_item: Mapped["TreatmentCatalogItem | None"] = relationship()
    vat_type: Mapped["VatType | None"] = relationship()
    treatment: Mapped["Treatment | None"] = relationship()

    __table_args__ = (
        Index("idx_budget_items_budget", "budget_id"),
        Index("idx_budget_items_catalog", "catalog_item_id"),
        Index("idx_budget_items_tooth", "budget_id", "tooth_number"),
        Index("idx_budget_items_treatment", "treatment_id"),
    )


class BudgetSignature(Base):
    """Digital signature record for budget acceptance.

    Stores signature data for audit trail and legal compliance.
    MVP uses click-to-accept; extensible for external providers.
    """

    __tablename__ = "budget_signatures"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), index=True)
    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), index=True
    )

    # Signature type
    signature_type: Mapped[str] = mapped_column(String(30))  # full_acceptance, rejection

    # Signed items (kept for historical records, all items now signed together)
    signed_items: Mapped[list | None] = mapped_column(JSONB, default=None)  # List of item IDs

    # Signer information
    signed_by_name: Mapped[str] = mapped_column(String(200))
    signed_by_email: Mapped[str | None] = mapped_column(String(255), default=None)
    relationship_to_patient: Mapped[str] = mapped_column(
        String(30)
    )  # patient, guardian, representative

    # Signature method
    signature_method: Mapped[str] = mapped_column(
        String(30)
    )  # click_accept, drawn, external_provider
    signature_data: Mapped[dict | None] = mapped_column(JSONB, default=None)  # Method-specific data

    # Audit information
    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)  # IPv4/IPv6
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # External provider integration (future)
    external_signature_id: Mapped[str | None] = mapped_column(String(255), default=None)
    external_provider: Mapped[str | None] = mapped_column(String(50), default=None)

    # Document integrity
    document_hash: Mapped[str | None] = mapped_column(String(64), default=None)  # SHA-256

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )

    # Relationships
    clinic: Mapped["Clinic"] = relationship()
    budget: Mapped["Budget"] = relationship(back_populates="signatures")

    __table_args__ = (
        Index("idx_budget_signatures_budget", "budget_id"),
        Index("idx_budget_signatures_clinic", "clinic_id"),
    )


class BudgetHistory(Base):
    """Audit log for budget changes.

    Records all changes to budgets for traceability and compliance.
    """

    __tablename__ = "budget_history"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), index=True)
    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), index=True
    )

    # Action performed
    action: Mapped[str] = mapped_column(
        String(30)
    )  # created, updated, status_changed, item_added, item_removed, signed, sent, duplicated

    # Actor
    changed_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # State snapshots
    previous_state: Mapped[dict | None] = mapped_column(JSONB, default=None)
    new_state: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # Additional context
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Relationships
    clinic: Mapped["Clinic"] = relationship()
    budget: Mapped["Budget"] = relationship(back_populates="history")
    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index("idx_budget_history_budget", "budget_id"),
        Index("idx_budget_history_clinic", "clinic_id"),
        Index("idx_budget_history_changed_at", "changed_at"),
    )
