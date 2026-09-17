from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FollowTask, Lead, NotificationEvent, Project
from app.services.event_bus import event_bus
from app.services.follow_task_reminders import FOLLOW_TASK_OVERDUE_EVENT, mark_due_follow_tasks


@pytest.fixture
def persisted_session_factory(tmp_path):
    """Use a disk-backed SQLite database to exercise real cross-session state."""

    engine = create_engine(f"sqlite:///{(tmp_path / 'follow-tasks.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _create_lead_and_tasks(factory, now: datetime):
    with factory() as db:
        project = Project(name="跟进提醒验收项目", industry="装修", location="长沙")
        db.add(project)
        db.flush()
        lead = Lead(project_id=project.id, platform="douyin", platform_user_id="reminder-user", nickname="待跟进客户")
        db.add(lead)
        db.flush()
        due = FollowTask(
            project_id=project.id,
            lead_id=lead.id,
            content="确认报价和进场时间",
            deadline=now - timedelta(seconds=1),
        )
        future = FollowTask(
            project_id=project.id,
            lead_id=lead.id,
            content="下周回访",
            deadline=now + timedelta(days=1),
        )
        completed = FollowTask(
            project_id=project.id,
            lead_id=lead.id,
            content="已完成的回访",
            deadline=now - timedelta(days=1),
            status="DONE",
        )
        db.add_all([due, future, completed])
        db.commit()
        return project.id, due.id, future.id, completed.id


def test_due_transition_and_reminder_are_durable_and_idempotent(persisted_session_factory):
    now = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)
    project_id, due_id, future_id, completed_id = _create_lead_and_tasks(persisted_session_factory, now)

    with persisted_session_factory() as db:
        reminders = mark_due_follow_tasks(db, now=now)
        assert len(reminders) == 1
        db.commit()

    # Reopen the database: the transition and event must survive the worker
    # session, not merely exist in its identity map.
    with persisted_session_factory() as db:
        due = db.get(FollowTask, due_id)
        assert due.status == "OVERDUE"
        assert due.overdue_at == now.replace(tzinfo=None)
        assert due.reminded_at == now.replace(tzinfo=None)
        assert due.reminder_count == 1
        events = db.scalars(select(NotificationEvent).where(NotificationEvent.follow_task_id == due_id)).all()
        assert len(events) == 1
        assert events[0].event_type == FOLLOW_TASK_OVERDUE_EVENT
        assert events[0].payload["status"] == "OVERDUE"

        # A later scheduler tick must not create a second event or increment
        # the durable counter again.
        assert mark_due_follow_tasks(db, now=now + timedelta(minutes=5)) == []
        db.commit()

        assert db.get(FollowTask, due_id).reminder_count == 1
        assert db.query(NotificationEvent).filter(NotificationEvent.follow_task_id == due_id).count() == 1
        assert db.get(FollowTask, future_id).status == "PENDING"
        assert db.get(FollowTask, completed_id).status == "DONE"


@pytest.mark.asyncio
async def test_scheduler_persists_and_publishes_overdue_event_once(monkeypatch, persisted_session_factory):
    from app.tasks import scheduler

    now = datetime.now(timezone.utc)
    project_id, due_id, _, _ = _create_lead_and_tasks(persisted_session_factory, now)
    monkeypatch.setattr(scheduler, "SessionLocal", persisted_session_factory)
    queue = event_bus.subscribe()
    try:
        assert await scheduler.process_due_follow_task_reminders() == 1
        event = await queue.get()
        assert event["event_type"] == FOLLOW_TASK_OVERDUE_EVENT
        assert event["project_id"] == project_id
        assert event["payload"]["follow_task_id"] == due_id
        assert await scheduler.process_due_follow_task_reminders() == 0
    finally:
        event_bus.unsubscribe(queue)


def test_follow_task_collection_api_has_project_status_filters_and_stable_contract(persisted_session_factory):
    from app import main

    now = datetime.now(timezone.utc)
    project_id, due_id, _, _ = _create_lead_and_tasks(persisted_session_factory, now)
    with persisted_session_factory() as db:
        mark_due_follow_tasks(db, now=now)
        db.commit()
        rows = main.list_all_follow_tasks(project_id=project_id, status="OVERDUE", db=db)
        payload = [main.FollowTaskOut.model_validate(row).model_dump() for row in rows]

    assert len(payload) == 1
    assert payload[0]["id"] == due_id
    assert set(payload[0]) == {
        "id",
        "lead_id",
        "project_id",
        "content",
        "deadline",
        "status",
        "completed_at",
        "overdue_at",
        "reminded_at",
        "reminder_count",
    }
    schema = main.app.openapi()["paths"]["/api/follow-tasks"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    item_schema = main.app.openapi()["components"]["schemas"]["FollowTaskOut"]["properties"]
    assert {"id", "lead_id", "project_id", "content", "deadline", "status", "completed_at", "overdue_at", "reminded_at", "reminder_count"} <= set(item_schema)
