from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, aliased

from app.models import Project, ScanTask, TaskCheckpoint, TaskStep, now_utc


TASK_STEP_NAMES = [
    "generate_keywords",
    "schedule_keywords",
    "scan_keyword",
    "discover_videos",
    "rank_videos",
    "scan_comments",
    "prefilter_comments",
    "judge_leads",
    "deduplicate_leads",
    "update_dashboard",
]

MIN_SCHEDULE_INTERVAL_MINUTES = 10
MAX_SCHEDULE_INTERVAL_MINUTES = 30
TASK_RUNTIME_INITIALIZING = "initializing_runtime"
TASK_RUNTIME_INIT_TIMEOUT_SECONDS = 60


def enqueue_scan(db: Session, project_id: int, full: bool = False, *, commit: bool = True) -> ScanTask:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")
    task = ScanTask(project_id=project_id, name=f"{project.location}{project.industry}行业扫描", status="queued", full=full)
    db.add(task)
    db.flush()
    db.add_all([TaskStep(task_id=task.id, name=name) for name in TASK_STEP_NAMES])
    db.add(TaskCheckpoint(task_id=task.id))
    if commit:
        db.commit()
        db.refresh(task)
    return task


def claim_next_task(db: Session) -> tuple[int, bool] | None:
    """Atomically claim the oldest queued task for a project not already running."""
    queued = aliased(ScanTask)
    running = aliased(ScanTask)
    candidate = db.scalar(
        select(queued)
        .where(
            queued.status == "queued",
            ~exists(select(running.id).where(running.project_id == queued.project_id, running.status == "running")),
        )
        .order_by(queued.created_at, queued.id)
        .limit(1)
    )
    if candidate is None:
        return None

    running_check = aliased(ScanTask)
    try:
        claimed = db.execute(
            update(ScanTask)
            .where(
                ScanTask.id == candidate.id,
                ScanTask.status == "queued",
                ~exists(select(running_check.id).where(running_check.project_id == candidate.project_id, running_check.status == "running")),
            )
            .values(status="running", current_step=TASK_RUNTIME_INITIALIZING, started_at=now_utc(), error="")
        )
        if claimed.rowcount != 1:
            db.rollback()
            return None
        db.commit()
    except Exception:
        # A failed claim must not leave the session holding an uncommitted
        # running transition. The caller may decide whether to retry.
        db.rollback()
        raise
    return candidate.id, bool(candidate.full)


def has_active_scan(db: Session, project_id: int) -> bool:
    return bool(db.scalar(select(ScanTask.id).where(ScanTask.project_id == project_id, ScanTask.status.in_(["queued", "running"])).limit(1)))


def advance_schedule(schedule, now):
    interval = max(MIN_SCHEDULE_INTERVAL_MINUTES, min(MAX_SCHEDULE_INTERVAL_MINUTES, schedule.interval_minutes))
    schedule.interval_minutes = interval
    schedule.last_run_at = now
    schedule.next_run_at = now + timedelta(minutes=interval)


def recover_stale_runtime_initializations(
    db: Session,
    *,
    now: datetime | None = None,
    timeout_seconds: int = TASK_RUNTIME_INIT_TIMEOUT_SECONDS,
) -> int:
    """Requeue tasks whose worker died before runtime initialization completed.

    ``claim_next_task`` marks this short-lived phase explicitly. A normal
    running task advances ``current_step`` before this lease expires, so the
    watchdog does not reset long-running scans merely because they take time.
    The API lifespan still performs the broader restart recovery for every
    running task.
    """

    current = now or now_utc()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    cutoff = current - timedelta(seconds=max(1, timeout_seconds))
    result = db.execute(
        update(ScanTask)
        .where(
            ScanTask.status == "running",
            ScanTask.current_step.in_(("", TASK_RUNTIME_INITIALIZING)),
            (ScanTask.started_at.is_(None) | (ScanTask.started_at <= cutoff)),
        )
        .values(status="queued", current_step="", started_at=None, error="运行时初始化中断，已重新排队")
    )
    return result.rowcount
