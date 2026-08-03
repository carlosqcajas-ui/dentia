"""Plan-derived budgets honour the clinic's manual-total policy (ADR 0018).

Two paths create a budget from a plan and both must respect the setting:

- ``POST /treatment-plans/{id}/confirm`` → ``create_from_plan_snapshot``
- ``POST /treatment-plans/{id}/generate-budget``

Plus the event handlers that mirror plan items into the budget, which
must leave a manual-total budget alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import Clinic, User
from app.core.auth.service import hash_password
from app.modules.budget.models import Budget, BudgetItem
from app.modules.budget.service import BudgetService
from app.modules.odontogram.models import Treatment
from app.modules.patients.models import Patient


async def _clinic(db: AsyncSession, *, manual_total: bool) -> tuple[Clinic, Patient, User]:
    """Clinic + patient + author. ``budgets.created_by`` is a real FK."""
    user = User(
        id=uuid4(),
        email=f"pro-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("TestPass1234"),
        first_name="Pro",
        last_name="Fesional",
    )
    db.add(user)
    await db.flush()

    clinic = Clinic(
        id=uuid4(),
        name="Clínica Plan",
        tax_id=f"B{uuid4().int % 100_000_000:08d}",
        timezone="America/La_Paz",
        currency="BOB",
        settings={"budget_manual_total_default": manual_total},
    )
    db.add(clinic)
    await db.flush()
    patient = Patient(id=uuid4(), clinic_id=clinic.id, first_name="Ana", last_name="García")
    db.add(patient)
    await db.commit()
    return clinic, patient, user


async def _treatments(db: AsyncSession, clinic, patient, count: int) -> list[Treatment]:
    """Real ``Treatment`` rows — ``budget_items.treatment_id`` is a FK."""
    rows = []
    for _ in range(count):
        t = Treatment(
            id=uuid4(),
            clinic_id=clinic.id,
            patient_id=patient.id,
            clinical_type="restorative",
            scope="tooth",
            status="planned",
            recorded_at=datetime.now(UTC),
        )
        db.add(t)
        rows.append(t)
    await db.flush()
    return rows


def _snapshot(patient_id, prices: list[str], treatments=None) -> dict:
    treatment_ids = (
        [str(t.id) for t in treatments] if treatments else [str(uuid4()) for _ in prices]
    )
    return {
        "plan_id": str(uuid4()),
        "patient_id": str(patient_id),
        "plan_number": "PL-0001",
        "items": [
            {
                "treatment_id": tid,
                "catalog_item_id": None,
                "description": f"Tratamiento {i}",
                "unit_price": p,
                "tooth_number": None,
                "surfaces": None,
            }
            for i, (p, tid) in enumerate(zip(prices, treatment_ids, strict=True), start=1)
        ],
    }


@pytest.mark.asyncio
async def test_confirm_creates_itemized_budget_by_default(db_session: AsyncSession):
    clinic, patient, user = await _clinic(db_session, manual_total=False)
    treatments = await _treatments(db_session, clinic, patient, 2)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["100.00", "50.00"], treatments)
    )
    await db_session.commit()

    assert budget is not None
    assert budget.is_manual_total is False
    items = (
        await db_session.execute(
            BudgetItem.__table__.select().where(BudgetItem.budget_id == budget.id)
        )
    ).all()
    assert len(items) == 2
    assert budget.total == Decimal("150.00")


@pytest.mark.asyncio
async def test_confirm_creates_manual_total_budget_when_clinic_opts_in(db_session: AsyncSession):
    """The professional owns the figure — seeded from the plan, no lines."""
    clinic, patient, user = await _clinic(db_session, manual_total=True)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["100.00", "50.00"])
    )
    await db_session.commit()

    assert budget is not None
    assert budget.is_manual_total is True
    # Seeded with the plan's sum so it starts as a correction, not blank.
    assert budget.total == Decimal("150.00")
    items = (
        await db_session.execute(
            BudgetItem.__table__.select().where(BudgetItem.budget_id == budget.id)
        )
    ).all()
    assert items == [], "a manual-total budget must carry no line items"


@pytest.mark.asyncio
async def test_manual_total_survives_recalculation(db_session: AsyncSession):
    """The typed figure must never be overwritten by item math."""
    clinic, patient, user = await _clinic(db_session, manual_total=True)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["100.00"])
    )
    assert budget is not None
    budget.total = Decimal("80.00")  # professional corrects it down
    await db_session.commit()

    await BudgetService._recalculate_totals(db_session, budget)
    await db_session.commit()

    refreshed = await db_session.get(Budget, budget.id)
    assert refreshed.total == Decimal("80.00")


@pytest.mark.asyncio
async def test_plan_item_events_skip_manual_total_budgets(db_session: AsyncSession):
    """Mirroring plan items into a manual-total budget would orphan rows."""
    from app.modules.budget import BudgetModule

    clinic, patient, user = await _clinic(db_session, manual_total=True)
    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["100.00"])
    )
    assert budget is not None
    await db_session.commit()

    module = BudgetModule()
    await module._on_treatment_added_to_plan(
        {
            "plan_id": str(uuid4()),
            "treatment_id": str(uuid4()),
            "clinic_id": str(clinic.id),
            "budget_id": str(budget.id),
            "catalog_item_id": None,
            "description": "Extra",
            "tooth_number": None,
            "surfaces": None,
            "unit_price": "42.00",
        }
    )

    items = (
        await db_session.execute(
            BudgetItem.__table__.select().where(BudgetItem.budget_id == budget.id)
        )
    ).all()
    assert items == [], "handler must not add items to a manual-total budget"

    refreshed = await db_session.get(Budget, budget.id)
    await db_session.refresh(refreshed)
    assert refreshed.total == Decimal("100.00"), "total must be untouched"


@pytest.mark.asyncio
async def test_manual_total_budget_lists_treatments_without_prices(db_session: AsyncSession):
    """The patient must read what is quoted; only the total carries a figure."""
    clinic, patient, user = await _clinic(db_session, manual_total=True)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["100.00", "50.00"])
    )
    await db_session.commit()

    assert budget is not None
    included = budget.included_items_snapshot
    assert included is not None and len(included) == 2

    for entry in included:
        assert "names" in entry
        # The whole point: no amount anywhere in the covered-items list.
        assert not any(k in entry for k in ("unit_price", "price", "total", "amount"))


@pytest.mark.asyncio
async def test_convert_draft_to_manual_total_keeps_names_drops_prices(db_session: AsyncSession):
    """An itemized draft can be switched over without losing the treatments."""
    clinic, patient, user = await _clinic(db_session, manual_total=False)
    treatments = await _treatments(db_session, clinic, patient, 2)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["120.00", "60.00"], treatments)
    )
    await db_session.commit()
    assert budget is not None and budget.is_manual_total is False
    assert budget.total == Decimal("180.00")

    await BudgetService.convert_to_manual_total(db_session, clinic.id, budget, user.id)
    await db_session.commit()

    assert budget.is_manual_total is True
    # The computed total carries over as the starting figure.
    assert budget.total == Decimal("180.00")
    # Lines are gone...
    rows = (
        await db_session.execute(
            BudgetItem.__table__.select().where(BudgetItem.budget_id == budget.id)
        )
    ).all()
    assert rows == []
    # ...but what the quote covers survives.
    assert budget.included_items_snapshot is not None
    assert len(budget.included_items_snapshot) == 2


@pytest.mark.asyncio
async def test_convert_refused_outside_draft(db_session: AsyncSession):
    """An accepted budget was shown to the patient — don't restructure it."""
    clinic, patient, user = await _clinic(db_session, manual_total=False)
    treatments = await _treatments(db_session, clinic, patient, 1)

    budget = await BudgetService.create_from_plan_snapshot(
        db_session, clinic.id, user.id, _snapshot(patient.id, ["90.00"], treatments)
    )
    assert budget is not None
    budget.status = "accepted"
    await db_session.commit()

    with pytest.raises(ValueError, match="draft"):
        await BudgetService.convert_to_manual_total(db_session, clinic.id, budget, user.id)
