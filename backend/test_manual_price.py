import asyncio
from decimal import Decimal
from uuid import UUID

from app.main import app
from app.core.plugins.loader import load_modules

load_modules(app)  # registers every module's models (Appointment, etc.)

from app.database import async_session_maker
from app.modules.odontogram.service import TreatmentService
from app.modules.treatment_plan.service import TreatmentPlanService

CLINIC_ID = UUID("12ba4984-0f81-4a00-a7e1-be3fad5bc803")
PATIENT_ID = UUID("828dc40c-5d05-48a4-b331-fdf85e8c76cb")
USER_ID = UUID("780ee00f-1f75-4099-bfc9-bf4ee8050ce3")
PLAN_ID = UUID("4541f466-28ce-4c3b-9fe1-c92f16fd1369")


async def main():
    async with async_session_maker() as db:
        # 1. Create a treatment with NO catalog item, manual price.
        treatment = await TreatmentService.create(
            db=db,
            clinic_id=CLINIC_ID,
            patient_id=PATIENT_ID,
            user_id=USER_ID,
            catalog_item_id=None,
            clinical_type="root_canal",
            tooth_numbers=[16],
            teeth=None,
            common_surfaces=None,
            status="planned",
            notes="Manual pricing test",
            budget_item_id=None,
            source_module="odontogram",
            scope="tooth",
            custom_price=Decimal("350.00"),
        )
        await db.commit()
        print(f"Created treatment {treatment.id} price_snapshot={treatment.price_snapshot} catalog_item_id={treatment.catalog_item_id}")

        # 2. Link it into the draft plan.
        item = await TreatmentPlanService.add_item(
            db, CLINIC_ID, PLAN_ID, {"treatment_id": treatment.id}
        )
        await db.commit()
        print(f"Linked as plan item {item.id}")

        # 3. Confirm the plan (draft -> pending), which auto-creates the budget.
        plan = await TreatmentPlanService.confirm(db, CLINIC_ID, PLAN_ID, USER_ID)
        await db.commit()
        print(f"Plan {plan.id} status={plan.status} budget_id={plan.budget_id}")

        # 4. Check the resulting budget's totals + items.
        from app.modules.budget.models import Budget

        budget = await db.get(Budget, plan.budget_id)
        await db.refresh(budget, ["items"])
        print(f"Budget {budget.id} subtotal={budget.subtotal} total={budget.total}")
        for bi in budget.items:
            print(f"  item {bi.id} catalog_item_id={bi.catalog_item_id} description={bi.description!r} unit_price={bi.unit_price} line_total={bi.line_total}")


asyncio.run(main())
