"""Budget module service layer - business logic."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.list_query import parse_sort
from app.modules.catalog.models import TreatmentCatalogItem, VatType

from .models import Budget, BudgetHistory, BudgetItem

# Public sort field → SQL column. ``payment_status`` is intentionally
# absent — that sort would require joining payments which violates
# budget's depends list. The frontend handles it in two steps when
# active.
_SORT_ALLOW = {
    "created_at": Budget.created_at,
    "valid_until": Budget.valid_until,
    "total": Budget.total,
    "status": Budget.status,
    "budget_number": Budget.budget_number,
}
_SORT_DEFAULT = "created_at:desc"


def _serialize_for_json(data: dict | None) -> dict | None:
    """Serialize dict values for JSON storage (handle date, datetime, Decimal, UUID)."""
    if data is None:
        return None

    result = {}
    for key, value in data.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = str(value)
        elif isinstance(value, UUID):
            result[key] = str(value)
        elif value is None:
            result[key] = None
        else:
            result[key] = value
    return result


class BudgetNumberService:
    """Service for generating budget numbers."""

    @staticmethod
    async def generate_number(
        db: AsyncSession,
        clinic_id: UUID,
        prefix: str = "PRES",
    ) -> str:
        """Generate the next budget number for a clinic.

        Format: {PREFIX}-{YEAR}-{SEQUENCE:04d}
        Example: PRES-2024-0001
        """
        current_year = datetime.now(UTC).year
        year_prefix = f"{prefix}-{current_year}-"

        # Find the highest number for this year
        result = await db.execute(
            select(func.max(Budget.budget_number)).where(
                Budget.clinic_id == clinic_id,
                Budget.budget_number.like(f"{year_prefix}%"),
            )
        )
        max_number = result.scalar_one_or_none()

        if max_number:
            # Extract sequence and increment
            try:
                sequence = int(max_number.split("-")[-1]) + 1
            except ValueError:
                sequence = 1
        else:
            sequence = 1

        return f"{year_prefix}{sequence:04d}"


class BudgetItemService:
    """Service for budget item operations."""

    @staticmethod
    async def create_item(
        db: AsyncSession,
        clinic_id: UUID,
        budget_id: UUID,
        data: dict,
    ) -> BudgetItem:
        """Create a budget item with price snapshot.

        ``catalog_item_id`` is optional — clinics that price case-by-case
        can pass ``description`` + ``unit_price`` instead. When
        ``catalog_item_id`` is absent, ``unit_price`` is required (there's
        no catalog default to fall back to).
        """
        catalog_item_id = data.get("catalog_item_id")
        catalog_item: TreatmentCatalogItem | None = None
        vat_type_id = None
        vat_rate = 0.0

        if catalog_item_id is not None:
            catalog_item = await db.get(TreatmentCatalogItem, catalog_item_id)
            if not catalog_item or catalog_item.clinic_id != clinic_id:
                raise ValueError("Invalid catalog item")
            if catalog_item.vat_type_id:
                vat_type = await db.get(VatType, catalog_item.vat_type_id)
                if vat_type:
                    vat_type_id = vat_type.id
                    vat_rate = vat_type.rate

        # Snapshot the unit price if not provided
        unit_price = data.get("unit_price")
        if unit_price is None:
            if catalog_item is not None:
                unit_price = catalog_item.default_price or Decimal("0.00")
            else:
                raise ValueError("unit_price is required for items without a catalog_item_id")

        description = data.get("description")
        if catalog_item is None and not description:
            raise ValueError("description is required for items without a catalog_item_id")

        # Create item
        item = BudgetItem(
            clinic_id=clinic_id,
            budget_id=budget_id,
            catalog_item_id=catalog_item_id,
            description=description,
            unit_price=unit_price,
            quantity=data.get("quantity", 1),
            discount_type=data.get("discount_type"),
            discount_value=data.get("discount_value"),
            vat_type_id=vat_type_id,
            vat_rate=vat_rate,
            tooth_number=data.get("tooth_number"),
            surfaces=data.get("surfaces"),
            treatment_id=data.get("treatment_id"),
            display_order=data.get("display_order", 0),
            notes=data.get("notes"),
        )

        # Calculate line totals
        BudgetItemService._calculate_line_totals(item)

        db.add(item)
        await db.flush()

        # Refresh with relationships for response
        await db.refresh(item, ["catalog_item", "vat_type"])

        return item

    @staticmethod
    async def update_item(
        db: AsyncSession,
        item: BudgetItem,
        data: dict,
    ) -> BudgetItem:
        """Update a budget item.

        Callers must have already rejected ``budget.is_manual_total``
        budgets — this layer trusts the caller (``BudgetService``) to
        enforce that invariant.
        """
        for key, value in data.items():
            if value is not None and hasattr(item, key):
                setattr(item, key, value)

        # Recalculate line totals
        BudgetItemService._calculate_line_totals(item)

        await db.flush()

        # Refresh with relationships for response
        await db.refresh(item, ["catalog_item", "vat_type"])

        return item

    @staticmethod
    async def delete_item(
        db: AsyncSession,
        item: BudgetItem,
    ) -> None:
        """Delete a budget item."""
        await db.delete(item)
        await db.flush()

    @staticmethod
    def _calculate_line_totals(item: BudgetItem) -> None:
        """Calculate line totals for an item."""
        # Line subtotal = unit_price * quantity
        item.line_subtotal = item.unit_price * item.quantity

        # Apply line discount
        item.line_discount = Decimal("0.00")
        if item.discount_value and item.discount_type:
            if item.discount_type == "percentage":
                item.line_discount = item.line_subtotal * (item.discount_value / Decimal("100"))
            else:  # absolute
                item.line_discount = min(item.discount_value, item.line_subtotal)

        # Taxable amount after discount
        taxable = item.line_subtotal - item.line_discount

        # Calculate tax
        item.line_tax = taxable * Decimal(str(item.vat_rate / 100))

        # Line total
        item.line_total = taxable + item.line_tax


class BudgetService:
    """Service for budget operations."""

    @staticmethod
    async def list_budgets(
        db: AsyncSession,
        clinic_id: UUID,
        page: int = 1,
        page_size: int = 20,
        patient_id: UUID | None = None,
        status: list[str] | None = None,
        created_by: UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        expired: bool | None = None,
        search: str | None = None,
        *,
        budget_ids: list[UUID] | None = None,
        assigned_professional_id: UUID | None = None,
        valid_until_before: date | None = None,
        valid_until_after: date | None = None,
        sort: str | None = None,
    ) -> tuple[list[Budget], int]:
        """List budgets with filtering and pagination.

        ``budget_ids`` is the cross-module intersection vector (e.g. the
        payments module returns the budget ids matching a payment-status
        filter; we intersect here). Empty list short-circuits to zero.
        """
        page_size = min(max(page_size, 1), 100)
        offset = (page - 1) * page_size

        # Empty intersection set → no rows.
        if budget_ids is not None and not budget_ids:
            return [], 0

        conditions = [
            Budget.clinic_id == clinic_id,
            Budget.deleted_at.is_(None),
        ]

        if budget_ids:
            conditions.append(Budget.id.in_(budget_ids))

        if patient_id:
            conditions.append(Budget.patient_id == patient_id)

        if status:
            conditions.append(Budget.status.in_(status))

        if created_by:
            conditions.append(Budget.created_by == created_by)

        if assigned_professional_id:
            conditions.append(Budget.assigned_professional_id == assigned_professional_id)

        if date_from:
            conditions.append(Budget.created_at >= datetime.combine(date_from, datetime.min.time()))

        if date_to:
            conditions.append(Budget.created_at <= datetime.combine(date_to, datetime.max.time()))

        if valid_until_before:
            conditions.append(Budget.valid_until <= valid_until_before)

        if valid_until_after:
            conditions.append(Budget.valid_until >= valid_until_after)

        if expired is not None:
            today = date.today()
            if expired:
                conditions.append(Budget.valid_until.isnot(None))
                conditions.append(Budget.valid_until < today)
            else:
                conditions.append(
                    or_(
                        Budget.valid_until.is_(None),
                        Budget.valid_until >= today,
                    )
                )

        if search:
            search_pattern = f"%{search}%"
            # Import Patient model for search
            from app.modules.patients.models import Patient

            # Subquery for patient name search
            patient_subq = (
                select(Patient.id)
                .where(
                    or_(
                        Patient.first_name.ilike(search_pattern),
                        Patient.last_name.ilike(search_pattern),
                        func.concat(Patient.first_name, " ", Patient.last_name).ilike(
                            search_pattern
                        ),
                    )
                )
                .scalar_subquery()
            )

            conditions.append(
                or_(
                    Budget.budget_number.ilike(search_pattern),
                    Budget.patient_id.in_(patient_subq),
                )
            )

        total = (await db.execute(select(func.count(Budget.id)).where(*conditions))).scalar() or 0

        query = (
            select(Budget)
            .where(*conditions)
            .options(
                joinedload(Budget.patient),
                joinedload(Budget.creator),
                joinedload(Budget.assigned_professional),
            )
            .order_by(parse_sort(sort, _SORT_ALLOW, _SORT_DEFAULT))
            .offset(offset)
            .limit(page_size)
        )

        result = await db.execute(query)
        budgets = result.unique().scalars().all()

        return list(budgets), total

    @staticmethod
    async def get_budget(
        db: AsyncSession,
        clinic_id: UUID,
        budget_id: UUID,
        include_items: bool = True,
    ) -> Budget | None:
        """Get a budget by ID with related data."""
        query = (
            select(Budget)
            .where(
                Budget.id == budget_id,
                Budget.clinic_id == clinic_id,
                Budget.deleted_at.is_(None),
            )
            .options(
                joinedload(Budget.patient),
                joinedload(Budget.creator),
                joinedload(Budget.assigned_professional),
            )
        )

        if include_items:
            query = query.options(
                joinedload(Budget.items).joinedload(BudgetItem.catalog_item),
                joinedload(Budget.items).joinedload(BudgetItem.vat_type),
                joinedload(Budget.signatures),
            )

        result = await db.execute(query)
        return result.unique().scalar_one_or_none()

    @staticmethod
    async def create_budget(
        db: AsyncSession,
        clinic_id: UUID,
        created_by: UUID,
        data: dict,
    ) -> Budget:
        """Create a new budget.

        Callers may pre-set ``data['budget_number']`` to override the
        auto-generated sequence — used by the migration importer so
        historic Gesdén presupuestos keep their original year+number
        instead of getting renumbered into the current year.
        """
        # Allow caller to pre-set the budget_number (migration import).
        # When absent or None, fall through to the regular generator.
        override = data.pop("budget_number", None)
        budget_number = override or await BudgetNumberService.generate_number(db, clinic_id)

        # Extract items data and the manual-total override (mutually
        # exclusive — a manual-total budget has no items to extract).
        items_data = data.pop("items", [])
        manual_total = data.pop("total", None)

        # Create budget
        budget = Budget(
            clinic_id=clinic_id,
            budget_number=budget_number,
            created_by=created_by,
            **data,
        )
        db.add(budget)
        await db.flush()

        if budget.is_manual_total:
            budget.total = manual_total or Decimal("0.00")
            budget.subtotal = budget.total
        else:
            # Create items
            for item_data in items_data:
                await BudgetItemService.create_item(db, clinic_id, budget.id, item_data)

            # Calculate totals
            await BudgetService._recalculate_totals(db, budget)

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=clinic_id,
            budget_id=budget.id,
            action="created",
            changed_by=created_by,
            new_state={"status": "draft", "total": str(budget.total)},
        )

        return budget

    @staticmethod
    async def create_from_plan_snapshot(
        db: AsyncSession,
        clinic_id: UUID,
        user_id: UUID,
        snapshot: dict,
    ) -> Budget | None:
        """Create the draft budget that mirrors a confirmed treatment plan.

        Called synchronously from ``TreatmentPlanService.confirm`` to
        guarantee atomicity. Idempotent: if a non-cancelled budget
        already exists for the plan (looked up by ``plan_id`` in the
        snapshot), returns ``None`` so the caller leaves the existing
        link alone.

        Snapshot shape: see
        ``TreatmentPlanService._build_plan_snapshot``.
        """
        from sqlalchemy import text

        from .workflow import DEFAULT_BUDGET_VALIDITY_DAYS, _resolve_clinic_settings

        plan_id_raw = snapshot.get("plan_id")
        patient_id_raw = snapshot.get("patient_id")
        plan_number = snapshot.get("plan_number")
        if not plan_id_raw or not patient_id_raw:
            return None

        # Idempotency: check whether a non-cancelled budget already
        # exists for this plan. We reverse-lookup via the
        # treatment_plans table to keep the dependency one-way.
        existing_row = (
            await db.execute(
                text(
                    "SELECT b.id FROM budgets b "
                    "JOIN treatment_plans tp ON tp.budget_id = b.id "
                    "WHERE tp.id = :plan_id "
                    "  AND tp.clinic_id = :clinic_id "
                    "  AND b.status != 'cancelled' "
                    "LIMIT 1"
                ),
                {"plan_id": plan_id_raw, "clinic_id": clinic_id},
            )
        ).first()
        if existing_row is not None:
            existing = await db.get(Budget, existing_row.id)
            return existing

        clinic_settings = await _resolve_clinic_settings(db, clinic_id)
        validity_days = int(clinic_settings.get("budget_expiry_days", DEFAULT_BUDGET_VALIDITY_DAYS))
        today = date.today()

        budget_number = await BudgetNumberService.generate_number(db, clinic_id)

        budget = Budget(
            clinic_id=clinic_id,
            patient_id=UUID(patient_id_raw),
            budget_number=budget_number,
            version=1,
            status="draft",
            valid_from=today,
            valid_until=today + timedelta(days=validity_days),
            created_by=user_id,
            plan_number_snapshot=plan_number,
            plan_status_snapshot="pending",
        )
        db.add(budget)
        await db.flush()

        for item_snapshot in snapshot.get("items") or []:
            treatment_id_raw = item_snapshot.get("treatment_id")
            unit_price_raw = item_snapshot.get("unit_price")
            catalog_item_id_raw = item_snapshot.get("catalog_item_id")
            if not treatment_id_raw or unit_price_raw is None:
                # No treatment to link, or no price at all (neither catalog
                # nor manual) — nothing sensible to bill yet.
                continue
            await BudgetItemService.create_item(
                db,
                clinic_id,
                budget.id,
                {
                    "catalog_item_id": UUID(catalog_item_id_raw) if catalog_item_id_raw else None,
                    "description": item_snapshot.get("description"),
                    "quantity": 1,
                    "treatment_id": UUID(treatment_id_raw),
                    "tooth_number": item_snapshot.get("tooth_number"),
                    "surfaces": item_snapshot.get("surfaces"),
                    "unit_price": Decimal(unit_price_raw),
                },
            )

        await BudgetService._recalculate_totals(db, budget)

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=clinic_id,
            budget_id=budget.id,
            action="created",
            changed_by=user_id,
            new_state={
                "status": "draft",
                "from_plan_id": plan_id_raw,
                "from_plan_number": plan_number,
            },
            notes="Auto-created from confirmed treatment plan",
        )
        await db.flush()
        return budget

    @staticmethod
    async def update_budget(
        db: AsyncSession,
        budget: Budget,
        data: dict,
        updated_by: UUID,
    ) -> Budget:
        """Update a budget (only allowed in draft status)."""
        if budget.status != "draft":
            raise ValueError("Only draft budgets can be edited")

        # Store previous state for history
        previous_state = {
            "valid_from": str(budget.valid_from) if budget.valid_from else None,
            "valid_until": str(budget.valid_until) if budget.valid_until else None,
            "global_discount_type": budget.global_discount_type,
            "global_discount_value": str(budget.global_discount_value)
            if budget.global_discount_value
            else None,
        }

        # Update fields
        for key, value in data.items():
            if value is not None and hasattr(budget, key):
                setattr(budget, key, value)

        # Recalculate totals (in case discount changed)
        await BudgetService._recalculate_totals(db, budget)

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="updated",
            changed_by=updated_by,
            previous_state=previous_state,
            new_state=data,
        )

        await db.flush()
        return budget

    @staticmethod
    async def delete_budget(
        db: AsyncSession,
        budget: Budget,
        deleted_by: UUID,
    ) -> None:
        """Soft delete a budget."""
        budget.deleted_at = datetime.now(UTC)

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="deleted",
            changed_by=deleted_by,
        )

        await db.flush()

    @staticmethod
    async def add_item(
        db: AsyncSession,
        budget: Budget,
        item_data: dict,
        added_by: UUID,
    ) -> BudgetItem:
        """Add an item to a budget."""
        if budget.is_manual_total:
            raise ValueError("Manual-total budgets don't have line items")
        if budget.status != "draft":
            raise ValueError("Items can only be added to draft budgets")

        item = await BudgetItemService.create_item(db, budget.clinic_id, budget.id, item_data)

        # Recalculate totals
        await BudgetService._recalculate_totals(db, budget)

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="item_added",
            changed_by=added_by,
            new_state={
                "item_id": str(item.id),
                "catalog_item_id": str(item.catalog_item_id) if item.catalog_item_id else None,
                "line_total": str(item.line_total),
            },
        )

        return item

    @staticmethod
    async def remove_item(
        db: AsyncSession,
        budget: Budget,
        item: BudgetItem,
        removed_by: UUID,
    ) -> None:
        """Remove an item from a budget."""
        if budget.is_manual_total:
            raise ValueError("Manual-total budgets don't have line items")
        if budget.status != "draft":
            raise ValueError("Items can only be removed from draft budgets")

        item_id = item.id
        await BudgetItemService.delete_item(db, item)

        # Recalculate totals
        await BudgetService._recalculate_totals(db, budget)

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="item_removed",
            changed_by=removed_by,
            previous_state={"item_id": str(item_id)},
        )

    @staticmethod
    async def set_manual_total(
        db: AsyncSession,
        budget: Budget,
        total: Decimal,
        updated_by: UUID,
    ) -> Budget:
        """Set the total on a manual-total budget by hand.

        Allowed in any non-deleted status — including ``accepted`` —
        because clinics without a fiscal invoicing integration need to
        correct a quote after the patient has already signed it. This
        intentionally breaks the signed PDF's tamper-evidence guarantee
        for this budget: ``document_hash`` is left untouched (it still
        reflects the total at signing time) and the discrepancy is only
        visible via this history entry. See CLAUDE.md gotchas.
        """
        if not budget.is_manual_total:
            raise ValueError("Only manual-total budgets support set_manual_total")

        previous_total = budget.total
        budget.total = total
        budget.subtotal = total
        budget.total_discount = Decimal("0.00")
        budget.total_tax = Decimal("0.00")

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="total_updated",
            changed_by=updated_by,
            previous_state={"total": str(previous_total), "status": budget.status},
            new_state={"total": str(total), "status": budget.status},
        )

        await db.flush()
        return budget

    @staticmethod
    async def duplicate_budget(
        db: AsyncSession,
        source_budget: Budget,
        created_by: UUID,
    ) -> Budget:
        """Create a new version of a budget."""
        # Get the latest version for this budget number
        result = await db.execute(
            select(func.max(Budget.version)).where(
                Budget.clinic_id == source_budget.clinic_id,
                Budget.budget_number == source_budget.budget_number,
            )
        )
        max_version = result.scalar_one_or_none() or 0

        # Create new budget
        new_budget = Budget(
            clinic_id=source_budget.clinic_id,
            patient_id=source_budget.patient_id,
            budget_number=source_budget.budget_number,
            version=max_version + 1,
            parent_budget_id=source_budget.id,
            status="draft",
            valid_from=date.today(),
            valid_until=source_budget.valid_until,
            created_by=created_by,
            assigned_professional_id=source_budget.assigned_professional_id,
            global_discount_type=source_budget.global_discount_type,
            global_discount_value=source_budget.global_discount_value,
            internal_notes=source_budget.internal_notes,
            patient_notes=source_budget.patient_notes,
            is_manual_total=source_budget.is_manual_total,
        )
        db.add(new_budget)
        await db.flush()

        if source_budget.is_manual_total:
            new_budget.total = source_budget.total
            new_budget.subtotal = source_budget.total

            await BudgetHistoryService.add_entry(
                db,
                clinic_id=new_budget.clinic_id,
                budget_id=new_budget.id,
                action="duplicated",
                changed_by=created_by,
                previous_state={"source_budget_id": str(source_budget.id)},
                new_state={"version": new_budget.version},
            )

            return new_budget

        # Copy items
        for source_item in source_budget.items:
            new_item = BudgetItem(
                clinic_id=source_budget.clinic_id,
                budget_id=new_budget.id,
                catalog_item_id=source_item.catalog_item_id,
                description=source_item.description,
                unit_price=source_item.unit_price,
                quantity=source_item.quantity,
                discount_type=source_item.discount_type,
                discount_value=source_item.discount_value,
                vat_type_id=source_item.vat_type_id,
                vat_rate=source_item.vat_rate,
                line_subtotal=source_item.line_subtotal,
                line_discount=source_item.line_discount,
                line_tax=source_item.line_tax,
                line_total=source_item.line_total,
                tooth_number=source_item.tooth_number,
                surfaces=source_item.surfaces,
                display_order=source_item.display_order,
                notes=source_item.notes,
            )
            db.add(new_item)

        await db.flush()

        # Copy totals
        new_budget.subtotal = source_budget.subtotal
        new_budget.total_discount = source_budget.total_discount
        new_budget.total_tax = source_budget.total_tax
        new_budget.total = source_budget.total

        # Add history entry
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=new_budget.clinic_id,
            budget_id=new_budget.id,
            action="duplicated",
            changed_by=created_by,
            previous_state={"source_budget_id": str(source_budget.id)},
            new_state={"version": new_budget.version},
        )

        return new_budget

    @staticmethod
    async def get_versions(
        db: AsyncSession,
        clinic_id: UUID,
        budget_number: str,
    ) -> list[Budget]:
        """Get all versions of a budget."""
        result = await db.execute(
            select(Budget)
            .where(
                Budget.clinic_id == clinic_id,
                Budget.budget_number == budget_number,
                Budget.deleted_at.is_(None),
            )
            .order_by(Budget.version.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def _recalculate_totals(
        db: AsyncSession,
        budget: Budget,
    ) -> None:
        """Recalculate budget totals from items.

        No-op for ``is_manual_total`` budgets — their total is set
        directly via ``set_manual_total`` and must never be overwritten
        by item math (they have no items to sum in the first place).
        """
        if budget.is_manual_total:
            return

        # Reload items
        result = await db.execute(select(BudgetItem).where(BudgetItem.budget_id == budget.id))
        items = list(result.scalars().all())

        # Sum up line totals
        subtotal = sum((item.line_subtotal for item in items), Decimal("0.00"))
        total_line_discount = sum((item.line_discount for item in items), Decimal("0.00"))
        total_tax = sum((item.line_tax for item in items), Decimal("0.00"))
        items_total = sum((item.line_total for item in items), Decimal("0.00"))

        # Apply global discount
        global_discount = Decimal("0.00")
        if budget.global_discount_value and budget.global_discount_type:
            if budget.global_discount_type == "percentage":
                global_discount = items_total * (budget.global_discount_value / Decimal("100"))
            else:  # absolute
                global_discount = min(budget.global_discount_value, items_total)

        # Update budget totals
        budget.subtotal = subtotal
        budget.total_discount = total_line_discount + global_discount
        budget.total_tax = total_tax
        budget.total = items_total - global_discount

    @staticmethod
    async def on_treatment_performed(data: dict) -> None:
        """Event handler for when a treatment is performed in odontogram.

        Updates the linked budget item status if applicable.
        """
        # This will be called by the event bus when odontogram.treatment.performed is published
        budget_item_id = data.get("budget_item_id")
        if not budget_item_id:
            return

        # Get DB session from context (this would need to be passed differently in real impl)
        # For now, this is a placeholder for the event handler
        pass


class BudgetHistoryService:
    """Service for budget history/audit operations."""

    @staticmethod
    async def add_entry(
        db: AsyncSession,
        clinic_id: UUID,
        budget_id: UUID,
        action: str,
        changed_by: UUID,
        previous_state: dict | None = None,
        new_state: dict | None = None,
        notes: str | None = None,
    ) -> BudgetHistory:
        """Add a history entry for a budget."""
        entry = BudgetHistory(
            clinic_id=clinic_id,
            budget_id=budget_id,
            action=action,
            changed_by=changed_by,
            changed_at=datetime.now(UTC),
            previous_state=_serialize_for_json(previous_state),
            new_state=_serialize_for_json(new_state),
            notes=notes,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def get_history(
        db: AsyncSession,
        clinic_id: UUID,
        budget_id: UUID,
    ) -> list[BudgetHistory]:
        """Get history for a budget."""
        result = await db.execute(
            select(BudgetHistory)
            .where(
                BudgetHistory.clinic_id == clinic_id,
                BudgetHistory.budget_id == budget_id,
            )
            .options(joinedload(BudgetHistory.user))
            .order_by(BudgetHistory.changed_at.desc())
        )
        return list(result.unique().scalars().all())
