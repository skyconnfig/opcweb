from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def enqueue_scan(db: Session, project_id: int, full: bool = False) -> ScanTask:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")
    task = ScanTask(project_id=project_id, name=f"{project.location}{project.industry}行业扫描", status="queued", full=full)
    db.add(task)
    db.flush()
    db.add_all([TaskStep(task_id=task.id, name=name) for name in TASK_STEP_NAMES])
    db.add(TaskCheckpoint(task_id=task.id))
    db.commit()
    db.refresh(task)
    return task


def has_active_scan(db: Session, project_id: int) -> bool:
    return bool(db.scalar(select(ScanTask.id).where(ScanTask.project_id == project_id, ScanTask.status.in_(["queued", "running"])).limit(1)))


def advance_schedule(schedule, now):
    schedule.last_run_at = now
    schedule.next_run_at = now + timedelta(minutes=max(15, schedule.interval_minutes))
