"""Durable, human-facing reminders for overdue follow-up tasks."""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import FollowTask, NotificationEvent, now_utc


FOLLOW_TASK_OVERDUE_EVENT = "follow_task.overdue"


def _utc_naive(value: datetime) -> datetime:
    """Normalize a timestamp for the project's UTC ``DateTime`` columns."""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_iso(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()


def mark_due_follow_tasks(db: Session, now: datetime | None = None) -> list[NotificationEvent]:
    """Transition due ``PENDING`` tasks and create one durable reminder each.

    The caller owns the transaction.  The conditional UPDATE is the claim
    boundary: only the worker that changes a task from ``PENDING`` can create
    its reminder.  ``reminded_at`` and the unique database key provide a
    second idempotency guard across scheduler restarts and processes.
    """

    checked_at = _utc_naive(now or now_utc())
    due_tasks = db.scalars(
        select(FollowTask)
        .where(
            FollowTask.status == "PENDING",
            FollowTask.deadline <= checked_at,
            FollowTask.reminded_at.is_(None),
        )
        .order_by(FollowTask.id)
    ).all()

    reminders: list[NotificationEvent] = []
    for task in due_tasks:
        claimed = db.execute(
            update(FollowTask)
            .where(
                FollowTask.id == task.id,
                FollowTask.status == "PENDING",
                FollowTask.reminded_at.is_(None),
            )
            .values(
                status="OVERDUE",
                overdue_at=checked_at,
                reminded_at=checked_at,
                reminder_count=(FollowTask.reminder_count + 1),
                updated_at=checked_at,
            )
        )
        if claimed.rowcount != 1:
            # Another worker won the conditional claim after the candidate
            # query.  It owns the notification as well.
            continue

        event = NotificationEvent(
            project_id=task.project_id,
            lead_id=task.lead_id,
            follow_task_id=task.id,
            event_type=FOLLOW_TASK_OVERDUE_EVENT,
            title="跟进任务已逾期",
            message=f"跟进任务“{task.content}”已到期，请及时处理。",
            payload={
                "follow_task_id": task.id,
                "lead_id": task.lead_id,
                "deadline": _utc_iso(task.deadline),
                "status": "OVERDUE",
            },
        )
        db.add(event)
        reminders.append(event)

    return reminders
