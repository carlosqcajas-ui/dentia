"""Budget workflow service - state machine and status transitions."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventType, event_bus

from .models import Budget, BudgetSignature
from .service import BudgetHistoryService

logger = logging.getLogger(__name__)

# Valid status transitions. ``completed`` is no longer a reachable
# status — kept as a no-op terminal entry so existing rows persisted
# from older versions still satisfy the can_transition lookup
# without surfacing in any new flow.
VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["sent", "accepted", "rejected", "cancelled"],
    "sent": ["accepted", "rejected", "expired", "cancelled"],
    "accepted": ["cancelled"],
    "completed": [],  # Legacy terminal state — no new transitions land here
    "rejected": [],  # Terminal state
    "expired": [],  # Terminal state
    "cancelled": [],  # Terminal state
}

# Default budget validity period when the clinic has no override.
DEFAULT_BUDGET_VALIDITY_DAYS = 30
# Default plan auto-close window after the budget has expired.
DEFAULT_PLAN_AUTO_CLOSE_DAYS = 30


async def _resolve_clinic_settings(db: AsyncSession, clinic_id: UUID) -> dict:
    """Read ``clinic.settings`` JSONB for budget-related toggles.

    Implemented with raw SQL to avoid importing the auth.Clinic model
    (which is core, but the lookup is settings-specific and we want a
    single small read).
    """
    row = (
        await db.execute(
            text("SELECT settings FROM clinics WHERE id = :id"),
            {"id": clinic_id},
        )
    ).first()
    return (row.settings if row and row.settings else {}) or {}


class BudgetWorkflowError(Exception):
    """Exception for workflow validation errors."""

    pass


class BudgetWorkflowService:
    """Service for budget status transitions and workflow operations."""

    @staticmethod
    def can_transition(current_status: str, new_status: str) -> bool:
        """Check if a status transition is valid."""
        allowed = VALID_TRANSITIONS.get(current_status, [])
        return new_status in allowed

    @staticmethod
    async def send_budget(
        db: AsyncSession,
        budget: Budget,
        sent_by: UUID,
        send_method: str = "manual",  # "manual" or "email"
        recipient_email: str | None = None,
        custom_message: str | None = None,
    ) -> Budget:
        """Mark budget as sent to patient.

        Args:
            db: Database session.
            budget: Budget to send.
            sent_by: User ID who triggered the send.
            send_method: How it was sent ("manual" for printed/handed, "email" for email).
            recipient_email: Email address if sent by email.
        """
        if not BudgetWorkflowService.can_transition(budget.status, "sent"):
            raise BudgetWorkflowError(f"Cannot send budget from status '{budget.status}'")

        # Check budget has items (or a positive manual total)
        if not budget.items and not (budget.is_manual_total and budget.total > 0):
            raise BudgetWorkflowError("Cannot send empty budget")

        previous_status = budget.status
        budget.status = "sent"

        # Add history
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="status_changed",
            changed_by=sent_by,
            previous_state={"status": previous_status},
            new_state={"status": "sent"},
        )

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="sent",
            changed_by=sent_by,
            new_state={
                "sent_at": datetime.now(UTC).isoformat(),
                "send_method": send_method,
                "recipient_email": recipient_email,
            },
        )

        await db.flush()

        # Publish event for notifications + timeline modules
        await event_bus.publish(
            EventType.BUDGET_SENT,
            {
                "clinic_id": str(budget.clinic_id),
                "budget_id": str(budget.id),
                "patient_id": str(budget.patient_id),
                "budget_number": budget.budget_number,
                "total": str(budget.total) if budget.total is not None else None,
                "send_method": send_method,
                "recipient_email": recipient_email,
                "custom_message": custom_message,
            },
        )

        return budget

    @staticmethod
    async def accept_budget(
        db: AsyncSession,
        budget: Budget,
        signature_data: dict,
        accepted_by: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        accepted_via: str = "manual",
    ) -> Budget:
        """Accept budget with signature.

        Args:
            accepted_via: ``remote_link`` | ``in_clinic`` | ``manual``.
                Persisted on ``Budget.accepted_via`` so reception can
                see how each acceptance was captured without joining
                signatures.
        """
        if not BudgetWorkflowService.can_transition(budget.status, "accepted"):
            raise BudgetWorkflowError(f"Cannot accept budget from status '{budget.status}'")

        # Check budget has items (or a positive manual total)
        if not budget.items and not (budget.is_manual_total and budget.total > 0):
            raise BudgetWorkflowError("Cannot accept empty budget")

        previous_status = budget.status
        budget.status = "accepted"
        budget.accepted_via = accepted_via

        # Create signature record
        signature = BudgetSignature(
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            signature_type="full_acceptance",
            signed_items=[str(item.id) for item in budget.items],
            signed_by_name=signature_data["signed_by_name"],
            signed_by_email=signature_data.get("signed_by_email"),
            relationship_to_patient=signature_data.get("relationship_to_patient", "patient"),
            signature_method=signature_data.get("signature_method", "click_accept"),
            signature_data=signature_data.get("signature_data"),
            ip_address=ip_address,
            user_agent=user_agent,
            signed_at=datetime.now(UTC),
        )
        db.add(signature)

        # Add history
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="status_changed",
            changed_by=accepted_by,
            previous_state={"status": previous_status},
            new_state={"status": "accepted"},
        )

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="signed",
            changed_by=accepted_by,
            new_state={
                "signature_type": "full_acceptance",
                "signed_by": signature_data["signed_by_name"],
            },
        )

        await db.flush()

        # Render the signed PDF once and persist its SHA-256 hash on
        # the signature row. Lets compliance tooling later detect any
        # tampering with stored / re-rendered PDFs. Best-effort:
        # WeasyPrint failures (e.g. missing system fonts in dev) must
        # not block the acceptance — log and continue.
        try:
            from .pdf import BudgetPDFService

            clinic_row = await db.execute(
                text(
                    "SELECT id, name, address, phone, email, settings, "
                    "tax_id, legal_name FROM clinics WHERE id = :id"
                ),
                {"id": budget.clinic_id},
            )
            clinic = clinic_row.first()
            if clinic is not None:
                clinic_settings = (
                    clinic.settings if isinstance(clinic.settings, dict) else {}
                ) or {}
                locale = str(clinic_settings.get("communication_language") or "es")
                # Re-fetch budget with items eagerly loaded for the PDF.
                from sqlalchemy.orm import selectinload

                from .models import Budget as _Budget

                hydrated = (
                    await db.execute(
                        select(_Budget)
                        .options(selectinload(_Budget.items))
                        .where(_Budget.id == budget.id)
                    )
                ).scalar_one_or_none()
                if hydrated is not None:
                    pdf_bytes = await BudgetPDFService.generate_pdf(
                        hydrated,
                        clinic,  # type: ignore[arg-type]
                        is_preview=False,
                        locale=locale,
                        signature=signature,
                    )
                    signature.document_hash = BudgetPDFService.generate_pdf_hash(pdf_bytes)
                    await db.flush()
        except Exception as exc:
            logger.warning(
                "Could not compute document_hash for budget %s: %s",
                budget.id,
                exc,
            )

        plan_id = await BudgetWorkflowService._lookup_plan_id(db, budget.id)
        await event_bus.publish(
            EventType.BUDGET_ACCEPTED,
            {
                "clinic_id": str(budget.clinic_id),
                "budget_id": str(budget.id),
                "patient_id": str(budget.patient_id),
                "budget_number": budget.budget_number,
                "total": str(budget.total) if budget.total is not None else None,
                "accepted_by": str(accepted_by),
                "accepted_via": accepted_via,
                "plan_id": str(plan_id) if plan_id else None,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

        return budget

    @staticmethod
    async def reject_budget(
        db: AsyncSession,
        budget: Budget,
        rejected_by: UUID | None = None,
        reason: str | None = None,
        note: str | None = None,
        signature_data: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Budget:
        """Reject a budget.

        Persists the rejection reason in ``Budget.rejection_reason`` and
        ``Budget.rejection_note`` (in addition to the history entry) so
        analytics queries do not need to parse history JSON. Publishes
        ``budget.rejected`` so the treatment_plan module can close the
        companion plan with ``closure_reason='rejected_by_patient'``.
        """
        if not BudgetWorkflowService.can_transition(budget.status, "rejected"):
            raise BudgetWorkflowError(f"Cannot reject budget from status '{budget.status}'")

        previous_status = budget.status
        budget.status = "rejected"
        if reason is not None:
            budget.rejection_reason = reason
        if note is not None:
            budget.rejection_note = note

        # Create signature record if provided
        if signature_data:
            signature = BudgetSignature(
                clinic_id=budget.clinic_id,
                budget_id=budget.id,
                signature_type="rejection",
                signed_by_name=signature_data["signed_by_name"],
                signed_by_email=signature_data.get("signed_by_email"),
                relationship_to_patient=signature_data.get("relationship_to_patient", "patient"),
                signature_method=signature_data.get("signature_method", "click_accept"),
                signature_data=signature_data.get("signature_data"),
                ip_address=ip_address,
                user_agent=user_agent,
                signed_at=datetime.now(UTC),
            )
            db.add(signature)

        # Add history
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="status_changed",
            changed_by=rejected_by or budget.created_by,
            previous_state={"status": previous_status},
            new_state={"status": "rejected", "reason": reason, "note": note},
        )

        await db.flush()

        plan_id = await BudgetWorkflowService._lookup_plan_id(db, budget.id)
        await event_bus.publish(
            EventType.BUDGET_REJECTED,
            {
                "clinic_id": str(budget.clinic_id),
                "budget_id": str(budget.id),
                "patient_id": str(budget.patient_id),
                "budget_number": budget.budget_number,
                "plan_id": str(plan_id) if plan_id else None,
                "rejection_reason": reason,
                "rejection_note": note,
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

        return budget

    @staticmethod
    async def cancel_budget(
        db: AsyncSession,
        budget: Budget,
        cancelled_by: UUID,
        reason: str | None = None,
    ) -> Budget:
        """Cancel a budget."""
        if not BudgetWorkflowService.can_transition(budget.status, "cancelled"):
            raise BudgetWorkflowError(f"Cannot cancel budget from status '{budget.status}'")

        previous_status = budget.status
        budget.status = "cancelled"

        # Add history
        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="status_changed",
            changed_by=cancelled_by,
            previous_state={"status": previous_status},
            new_state={"status": "cancelled", "reason": reason},
        )

        await db.flush()

        return budget

    @staticmethod
    async def check_expired_budgets(
        db: AsyncSession,
        clinic_id: UUID,
    ) -> list[Budget]:
        """Check for and mark expired budgets.

        Should be called periodically (e.g., daily cron job).
        Draft and sent budgets can expire (accepted budgets don't expire).
        Publishes ``budget.expired`` for each expired budget so the
        treatment_plan module can keep its pending plans visible with a
        "presupuesto caducado" badge until the auto-close cron kicks in.
        """
        from datetime import date

        today = date.today()

        # Find draft or sent budgets past their valid_until date
        result = await db.execute(
            select(Budget).where(
                Budget.clinic_id == clinic_id,
                Budget.status.in_(["draft", "sent"]),
                Budget.valid_until.isnot(None),
                Budget.valid_until < today,
                Budget.deleted_at.is_(None),
            )
        )

        expired_budgets = list(result.scalars().all())

        for budget in expired_budgets:
            previous_status = budget.status
            budget.status = "expired"

            # Add history (use creator as fallback)
            await BudgetHistoryService.add_entry(
                db,
                clinic_id=budget.clinic_id,
                budget_id=budget.id,
                action="status_changed",
                changed_by=budget.created_by,
                previous_state={"status": previous_status},
                new_state={"status": "expired", "expired_at": today.isoformat()},
                notes="Automatically expired due to validity period",
            )

        await db.flush()

        # Publish events for subscribers (treatment_plan, patient_timeline).
        for budget in expired_budgets:
            plan_id = await BudgetWorkflowService._lookup_plan_id(db, budget.id)
            days_overdue = (today - budget.valid_until).days if budget.valid_until else None
            await event_bus.publish(
                EventType.BUDGET_EXPIRED,
                {
                    "clinic_id": str(budget.clinic_id),
                    "budget_id": str(budget.id),
                    "patient_id": str(budget.patient_id),
                    "budget_number": budget.budget_number,
                    "plan_id": str(plan_id) if plan_id else None,
                    "valid_until": budget.valid_until.isoformat() if budget.valid_until else None,
                    "days_overdue": days_overdue,
                    "occurred_at": datetime.now(UTC).isoformat(),
                },
            )

        return expired_budgets

    # ---------------------------------------------------------------------
    # Renegotiation, public-link helpers and resend
    # ---------------------------------------------------------------------

    @staticmethod
    async def _lookup_plan_id(db: AsyncSession, budget_id: UUID) -> UUID | None:
        """Reverse-lookup the plan that references a budget.

        Implemented with raw SQL to avoid importing the
        ``treatment_plan`` ORM model from this module (ADR 0003).
        """
        row = (
            await db.execute(
                text(
                    "SELECT id FROM treatment_plans "
                    "WHERE budget_id = :bid AND deleted_at IS NULL "
                    "LIMIT 1"
                ),
                {"bid": budget_id},
            )
        ).first()
        return row.id if row else None

    @staticmethod
    async def cancel_for_renegotiation(
        db: AsyncSession,
        budget: Budget,
        cancelled_by: UUID,
    ) -> Budget:
        """Cancel a sent budget so reception can renegotiate.

        Publishes ``budget.renegotiated`` carrying ``plan_id`` so the
        treatment_plan handler can reopen the companion plan back to
        ``draft``. The reopen is event-driven specifically because
        ``treatment_plan`` is not in budget's ``manifest.depends`` —
        the reverse direction is correctly the bus.
        """
        if not BudgetWorkflowService.can_transition(budget.status, "cancelled"):
            raise BudgetWorkflowError(f"Cannot renegotiate budget in status '{budget.status}'")

        previous_status = budget.status
        budget.status = "cancelled"

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=budget.id,
            action="status_changed",
            changed_by=cancelled_by,
            previous_state={"status": previous_status},
            new_state={"status": "cancelled", "reason": "renegotiation"},
        )
        await db.flush()

        plan_id = await BudgetWorkflowService._lookup_plan_id(db, budget.id)
        await event_bus.publish(
            EventType.BUDGET_RENEGOTIATED,
            {
                "clinic_id": str(budget.clinic_id),
                "budget_id": str(budget.id),
                "patient_id": str(budget.patient_id),
                "plan_id": str(plan_id) if plan_id else None,
                "version": budget.version,
                "cancelled_at": datetime.now(UTC).isoformat(),
                "cancelled_by": str(cancelled_by),
            },
        )
        return budget

    @staticmethod
    async def clone_to_new_draft(
        db: AsyncSession,
        budget: Budget,
        cloned_by: UUID,
    ) -> Budget:
        """Clone a finished (rejected/expired/cancelled) budget to a new
        draft (version+1). Used by reception to "Resend" a budget.

        The new draft inherits the line items, valid window and plan
        snapshots.
        """
        from .service import BudgetItemService, BudgetService

        new_budget = Budget(
            clinic_id=budget.clinic_id,
            patient_id=budget.patient_id,
            budget_number=budget.budget_number,
            version=budget.version + 1,
            parent_budget_id=budget.id,
            status="draft",
            valid_from=datetime.now(UTC).date(),
            valid_until=budget.valid_until,
            created_by=cloned_by,
            assigned_professional_id=budget.assigned_professional_id,
            global_discount_type=budget.global_discount_type,
            global_discount_value=budget.global_discount_value,
            plan_number_snapshot=budget.plan_number_snapshot,
            plan_status_snapshot=budget.plan_status_snapshot,
            is_manual_total=budget.is_manual_total,
        )
        db.add(new_budget)
        await db.flush()

        if budget.is_manual_total:
            new_budget.total = budget.total
            new_budget.subtotal = budget.total

            await BudgetHistoryService.add_entry(
                db,
                clinic_id=budget.clinic_id,
                budget_id=new_budget.id,
                action="created",
                changed_by=cloned_by,
                previous_state={"parent_budget_id": str(budget.id)},
                new_state={"status": "draft", "version": new_budget.version},
                notes="Cloned from budget for resend",
            )
            await db.flush()
            return new_budget

        # Clone items (snapshot of catalog/treatment refs).
        for item in budget.items:
            await BudgetItemService.create_item(
                db,
                budget.clinic_id,
                new_budget.id,
                {
                    "catalog_item_id": item.catalog_item_id,
                    "quantity": item.quantity,
                    "treatment_id": item.treatment_id,
                    "tooth_number": item.tooth_number,
                    "surfaces": item.surfaces,
                    "unit_price": item.unit_price,
                    "discount_type": item.discount_type,
                    "discount_value": item.discount_value,
                    "vat_type_id": item.vat_type_id,
                    "vat_rate": item.vat_rate,
                    "display_order": item.display_order,
                    "notes": item.notes,
                },
            )
        await BudgetService._recalculate_totals(db, new_budget)

        await BudgetHistoryService.add_entry(
            db,
            clinic_id=budget.clinic_id,
            budget_id=new_budget.id,
            action="created",
            changed_by=cloned_by,
            previous_state={"parent_budget_id": str(budget.id)},
            new_state={"status": "draft", "version": new_budget.version},
            notes="Cloned from budget for resend",
        )
        await db.flush()
        return new_budget
