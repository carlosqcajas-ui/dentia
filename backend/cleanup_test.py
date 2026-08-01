import asyncio
from uuid import UUID

from app.main import app
from app.core.plugins.loader import load_modules

load_modules(app)

from app.database import async_session_maker
from sqlalchemy import text

BUDGET_ID = UUID("b387f99a-9f05-4521-b995-24b0a3c60ddc")
PLAN_ITEM_ID = UUID("716140c4-40b5-4840-a048-3f04e1e96c5f")
TREATMENT_ID = UUID("af0fd6ce-1fe9-4cc7-be2b-f734432772a2")
PLAN_ID = UUID("4541f466-28ce-4c3b-9fe1-c92f16fd1369")


async def main():
    async with async_session_maker() as db:
        await db.execute(
            text("UPDATE treatment_plans SET budget_id = NULL WHERE id = :id"),
            {"id": str(PLAN_ID)},
        )
        await db.execute(text("DELETE FROM budget_history WHERE budget_id = :id"), {"id": str(BUDGET_ID)})
        await db.execute(text("DELETE FROM budget_items WHERE budget_id = :id"), {"id": str(BUDGET_ID)})
        await db.execute(text("DELETE FROM budgets WHERE id = :id"), {"id": str(BUDGET_ID)})
        await db.execute(
            text("DELETE FROM planned_treatment_item_sessions WHERE plan_item_id = :id"),
            {"id": str(PLAN_ITEM_ID)},
        )
        await db.execute(text("DELETE FROM planned_treatment_items WHERE id = :id"), {"id": str(PLAN_ITEM_ID)})
        await db.execute(text("DELETE FROM treatment_teeth WHERE treatment_id = :id"), {"id": str(TREATMENT_ID)})
        await db.execute(text("DELETE FROM treatments WHERE id = :id"), {"id": str(TREATMENT_ID)})
        await db.execute(
            text("UPDATE treatment_plans SET status = 'draft', budget_id = NULL, confirmed_at = NULL WHERE id = :id"),
            {"id": str(PLAN_ID)},
        )
        await db.commit()
        print("Cleanup done")


asyncio.run(main())
