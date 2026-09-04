from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ScanSchedule
from app.tasks.queue import advance_schedule, enqueue_scan, has_active_scan


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="Asia/Shanghai")


async def enqueue_due_schedules() -> int:
    now = datetime.now(timezone.utc)
    created = 0
    with SessionLocal() as db:
        schedules = db.scalars(select(ScanSchedule).where(ScanSchedule.enabled.is_(True), ScanSchedule.next_run_at.is_not(None))).all()
        for schedule in schedules:
            due_at = schedule.next_run_at.replace(tzinfo=timezone.utc) if schedule.next_run_at and schedule.next_run_at.tzinfo is None else schedule.next_run_at
            if due_at and due_at > now:
                continue
            if not has_active_scan(db, schedule.project_id):
                enqueue_scan(db, schedule.project_id, schedule.full)
                created += 1
            advance_schedule(schedule, now)
        db.commit()
    return created
