"""Budget background tasks (APScheduler entry points).

These functions are called by ``app.core.scheduler``. Each opens its
own session and commits explicitly. They are idempotent — re-running
without state change is a no-op.

Per-clinic work runs concurrently behind an ``asyncio.Semaphore`` so
a slow clinic does not delay the rest. The cap (``_CLINIC_CONCURRENCY``)
stays comfortably under the DB pool size to avoid contention with
in-flight requests sharing the same engine.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import text

from app.database import async_session_maker

from .workflow import BudgetWorkflowService

logger = logging.getLogger(__name__)

_CLINIC_CONCURRENCY = 5


async def _list_clinics() -> list[UUID]:
    async with async_session_maker() as db:
        rows = (await db.execute(text("SELECT id FROM clinics WHERE deleted_at IS NULL"))).all()
    return [row.id for row in rows]


async def _expire_for_clinic(clinic_id: UUID, sem: asyncio.Semaphore) -> None:
    async with sem, async_session_maker() as db:
        try:
            await BudgetWorkflowService.check_expired_budgets(db, clinic_id)
            await db.commit()
        except Exception as exc:
            logger.error("expire_budgets failed for clinic %s: %s", clinic_id, exc, exc_info=True)
            await db.rollback()


async def expire_budgets() -> None:
    """Mark every draft/sent budget past ``valid_until`` as ``expired``.

    Wraps ``BudgetWorkflowService.check_expired_budgets`` per clinic.
    """
    clinic_ids = await _list_clinics()
    sem = asyncio.Semaphore(_CLINIC_CONCURRENCY)
    await asyncio.gather(
        *(_expire_for_clinic(cid, sem) for cid in clinic_ids),
        return_exceptions=False,
    )
