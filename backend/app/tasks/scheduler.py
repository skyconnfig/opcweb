from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ScanSchedule
from app.providers.base import BaseContentProvider
from app.tasks.queue import advance_schedule, enqueue_scan, has_active_scan, recover_stale_runtime_initializations


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="Asia/Shanghai")


async def enqueue_due_schedules(
    provider: BaseContentProvider | None = None,
    provider_resolver: Callable[[], BaseContentProvider] | None = None,
) -> int:
    """Enqueue due plans only when the real content source is healthy.

    The optional provider arguments keep this function easy to test while the
    production lifespan supplies the currently active provider. A failed
    health check leaves ``next_run_at`` untouched so the plan can retry on the
    next scheduler tick instead of creating a doomed scan task.
    """

    # Recover a worker that died after claiming a task but before it could
    # construct its Provider/LLM. Do this before resolving the Provider so a
    # broken configuration cannot also stop the watchdog.
    with SessionLocal() as db:
        recovered = recover_stale_runtime_initializations(db)
        if recovered:
            db.commit()

    if provider is None and provider_resolver is not None:
        try:
            provider = provider_resolver()
        except Exception:
            return 0
        if provider is None:
            return 0
    if provider is not None:
        try:
            health = await provider.health_check()
        except Exception:
            return 0
        if health.status != "connected":
            return 0

    now = datetime.now(timezone.utc)
    created = 0
    with SessionLocal() as db:
        # Treat an enabled schedule without a next-run timestamp as due. This
        # repairs legacy rows created before schedule timestamps were
        # persisted and prevents a UI-visible enabled plan from becoming a
        # silent no-op after migration.
        schedules = db.scalars(select(ScanSchedule).where(ScanSchedule.enabled.is_(True))).all()
        for schedule in schedules:
            due_at = schedule.next_run_at.replace(tzinfo=timezone.utc) if schedule.next_run_at and schedule.next_run_at.tzinfo is None else schedule.next_run_at
            if due_at is not None and due_at > now:
                continue
            if not has_active_scan(db, schedule.project_id):
                # Keep task creation and schedule advancement in one
                # transaction. A process exit between two commits otherwise
                # leaves the plan due and can create a duplicate later.
                enqueue_scan(db, schedule.project_id, schedule.full, commit=False)
                created += 1
            advance_schedule(schedule, now)
        db.commit()
    return created
