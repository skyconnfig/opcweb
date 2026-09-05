import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Project, ScanSchedule, ScanTask
from app.providers.base import ProviderHealth
from app.tasks import scheduler
from app.tasks.queue import advance_schedule, claim_next_task, enqueue_scan


class HealthyProvider:
    async def health_check(self):
        return ProviderHealth("connected", "test provider")


class UnhealthyProvider:
    async def health_check(self):
        return ProviderHealth("login_required", "login required")


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _project(factory, name="计划运行时项目"):
    with factory() as db:
        project = Project(name=name, industry="装修", location="长沙")
        db.add(project)
        db.commit()
        return project.id


def _due_schedule(factory, project_id, *, interval=10):
    with factory() as db:
        schedule = ScanSchedule(
            project_id=project_id,
            enabled=True,
            interval_minutes=interval,
            next_run_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        )
        db.add(schedule)
        db.commit()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(10, 10), (30, 30), (9, 10), (31, 30)],
)
def test_schedule_runtime_keeps_interval_inclusive_10_to_30(configured, expected):
    schedule = ScanSchedule(interval_minutes=configured)
    now = datetime.now(timezone.utc)

    advance_schedule(schedule, now)

    assert schedule.interval_minutes == expected
    assert schedule.next_run_at == now + timedelta(minutes=expected)


@pytest.mark.asyncio
async def test_unhealthy_provider_does_not_enqueue_or_advance_schedule(monkeypatch, session_factory):
    project_id = _project(session_factory)
    _due_schedule(session_factory, project_id)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)

    with session_factory() as db:
        before = db.scalar(select(ScanSchedule).where(ScanSchedule.project_id == project_id)).next_run_at

    assert await scheduler.enqueue_due_schedules(provider=UnhealthyProvider()) == 0

    with session_factory() as db:
        schedule = db.scalar(select(ScanSchedule).where(ScanSchedule.project_id == project_id))
        assert db.scalar(select(ScanTask).where(ScanTask.project_id == project_id)) is None
        assert schedule.next_run_at == before


@pytest.mark.asyncio
async def test_provider_resolver_initialization_failure_is_safe(monkeypatch, session_factory):
    project_id = _project(session_factory)
    _due_schedule(session_factory, project_id)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)

    def failing_resolver():
        raise RuntimeError("provider initialization failed")

    assert await scheduler.enqueue_due_schedules(provider_resolver=failing_resolver) == 0

    with session_factory() as db:
        assert db.scalar(select(ScanTask).where(ScanTask.project_id == project_id)) is None


@pytest.mark.asyncio
async def test_missing_provider_from_resolver_does_not_enqueue(monkeypatch, session_factory):
    project_id = _project(session_factory, "缺失数据源项目")
    _due_schedule(session_factory, project_id)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)

    assert await scheduler.enqueue_due_schedules(provider_resolver=lambda: None) == 0

    with session_factory() as db:
        assert db.scalar(select(ScanTask).where(ScanTask.project_id == project_id)) is None


@pytest.mark.asyncio
async def test_due_schedule_persists_task_and_next_run_together(monkeypatch, session_factory):
    project_id = _project(session_factory)
    _due_schedule(session_factory, project_id, interval=30)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)

    assert await scheduler.enqueue_due_schedules(provider=HealthyProvider()) == 1

    # Read through a new session: both records must have survived the
    # scheduler transaction, rather than only existing in its identity map.
    with session_factory() as db:
        schedule = db.scalar(select(ScanSchedule).where(ScanSchedule.project_id == project_id))
        task = db.scalar(select(ScanTask).where(ScanTask.project_id == project_id))
        assert task is not None
        assert task.status == "queued"
        assert schedule.last_run_at is not None
        assert schedule.next_run_at is not None
        assert schedule.next_run_at > schedule.last_run_at


@pytest.mark.asyncio
async def test_due_schedule_deduplicates_project_with_active_task(monkeypatch, session_factory):
    project_id = _project(session_factory)
    _due_schedule(session_factory, project_id)
    with session_factory() as db:
        existing = enqueue_scan(db, project_id)
        existing_id = existing.id
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)

    assert await scheduler.enqueue_due_schedules(provider=HealthyProvider()) == 0

    with session_factory() as db:
        tasks = db.scalars(select(ScanTask).where(ScanTask.project_id == project_id)).all()
        assert [task.id for task in tasks] == [existing_id]


def test_failed_claim_commit_rolls_back_running_transition(session_factory, monkeypatch):
    project_id = _project(session_factory)
    with session_factory() as db:
        task = enqueue_scan(db, project_id)
        task_id = task.id
        original_commit = db.commit

        def failing_commit():
            raise RuntimeError("claim commit failed")

        monkeypatch.setattr(db, "commit", failing_commit)
        with pytest.raises(RuntimeError, match="claim commit failed"):
            claim_next_task(db)

        monkeypatch.setattr(db, "commit", original_commit)
        assert db.get(ScanTask, task_id).status == "queued"
        assert db.get(ScanTask, task_id).current_step == ""


@pytest.mark.asyncio
async def test_stale_provider_or_llm_runtime_initialization_is_requeued(monkeypatch, session_factory):
    project_id = _project(session_factory)
    with session_factory() as db:
        task = enqueue_scan(db, project_id)
        assert claim_next_task(db) == (task.id, False)
        task = db.get(ScanTask, task.id)
        task.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=61)
        db.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)
    assert await scheduler.enqueue_due_schedules(provider=HealthyProvider()) == 0

    with session_factory() as db:
        recovered = db.get(ScanTask, task.id)
        assert recovered.status == "queued"
        assert recovered.current_step == ""
        assert recovered.started_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "llm"])
async def test_worker_runtime_initialization_exception_fails_task_without_killing_worker(monkeypatch, session_factory, failure):
    from app import main

    project_id = _project(session_factory, f"{failure}初始化异常项目")
    second_project_id = _project(session_factory, f"{failure}初始化后续项目")
    monkeypatch.setattr(main, "SessionLocal", session_factory)

    provider_attempts = 0
    llm_attempts = 0
    if failure == "provider":
        def provider(_db):
            nonlocal provider_attempts
            provider_attempts += 1
            if provider_attempts == 1:
                raise RuntimeError("provider init failed")
            return object()

        monkeypatch.setattr(main, "active_provider", provider)
        monkeypatch.setattr(main, "active_llm", lambda _db: object())
    else:
        monkeypatch.setattr(main, "active_provider", lambda _db: object())
        def llm(_db):
            nonlocal llm_attempts
            llm_attempts += 1
            if llm_attempts == 1:
                raise RuntimeError("llm init failed")
            return object()

        monkeypatch.setattr(main, "active_llm", llm)

    with session_factory() as db:
        enqueue_scan(db, project_id)
        second_task = enqueue_scan(db, second_project_id)

    stop = asyncio.Event()
    processed: list[int] = []

    class ProbeService:
        def __init__(self, provider, llm):
            pass

        async def run_task(self, task_id, full=False):
            processed.append(task_id)
            with session_factory() as db:
                task = db.get(ScanTask, task_id)
                task.status = "completed"
                db.commit()
            stop.set()

    monkeypatch.setattr(main, "RadarService", ProbeService)

    await main._task_worker(stop)

    with session_factory() as db:
        failed = db.scalar(select(ScanTask).where(ScanTask.project_id == project_id))
        assert failed.status == "failed"
        assert failed.current_step == "initializing_runtime"
        assert "初始化失败" in failed.error
        assert "init failed" in failed.error
        assert db.get(ScanTask, second_task.id).status == "completed"
    assert processed == [second_task.id]


@pytest.mark.asyncio
async def test_fresh_claim_and_started_step_are_not_requeued(monkeypatch, session_factory):
    first_project_id = _project(session_factory, "新领取任务")
    second_project_id = _project(session_factory, "已开始任务")
    with session_factory() as db:
        fresh = enqueue_scan(db, first_project_id)
        started = enqueue_scan(db, second_project_id)
        claim_next_task(db)
        claim_next_task(db)
        fresh = db.get(ScanTask, fresh.id)
        started = db.get(ScanTask, started.id)
        fresh.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        started.current_step = "scan_comments"
        started.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        db.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)
    assert await scheduler.enqueue_due_schedules(provider=HealthyProvider()) == 0

    with session_factory() as db:
        assert db.get(ScanTask, fresh.id).status == "running"
        assert db.get(ScanTask, started.id).status == "running"


@pytest.mark.asyncio
async def test_api_lifespan_requeues_running_tasks_after_restart(monkeypatch, session_factory):
    from app import main

    project_id = _project(session_factory, "重启恢复项目")
    with session_factory() as db:
        task = enqueue_scan(db, project_id)
        task.status = "running"
        task.current_step = "scan_comments"
        task.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        db.commit()
        task_id = task.id

    class FakeScheduler:
        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def shutdown(self, **kwargs):
            return None

    async def idle_worker(stop):
        await stop.wait()

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "init_database", lambda: None)
    monkeypatch.setattr(main, "provider_registry", lambda: [])
    monkeypatch.setattr(main, "create_scheduler", lambda: FakeScheduler())
    monkeypatch.setattr(main, "_task_worker", idle_worker)
    monkeypatch.setattr(main, "_douyin_provider", None)

    async with main.lifespan(main.app):
        with session_factory() as db:
            assert db.get(ScanTask, task_id).status == "queued"
