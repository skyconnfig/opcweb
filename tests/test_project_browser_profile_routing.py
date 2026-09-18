from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.core.config import Settings
from app.db import Base
from app.models import AgentRun, BrowserSession, Comment, CommentReply, FollowTask, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Project, ScanTask, TaskArtifact, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, Video, now_utc


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_new_project_gets_an_isolated_profile_path(tmp_path):
    db = _session()
    settings = Settings(douyin_profile_dir=str(tmp_path / "browser" / "douyin"))
    project = Project(name="新项目", industry="装修")
    db.add(project)
    db.flush()

    session = main._ensure_project_browser_session(db, project.id, settings, new_project=True)

    assert session.profile_path == str((tmp_path / "browser" / "projects" / f"project-{project.id}").resolve())
    assert session.project_id == project.id


def test_existing_legacy_profile_is_adopted_only_by_latest_project(tmp_path):
    db = _session()
    settings = Settings(douyin_profile_dir=str(tmp_path / "browser" / "douyin"))
    older = Project(name="旧项目", industry="装修", updated_at=now_utc() - timedelta(days=1))
    latest = Project(name="当前项目", industry="装修", updated_at=now_utc())
    db.add_all([older, latest])
    db.flush()
    db.add(BrowserSession(project_id=None, profile_path=str((tmp_path / "browser" / "douyin").resolve()), status="READY"))
    db.flush()

    latest_session = main._ensure_project_browser_session(db, latest.id, settings)
    older_session = main._ensure_project_browser_session(db, older.id, settings)

    assert latest_session.profile_path == str((tmp_path / "browser" / "douyin").resolve())
    assert older_session.profile_path == str((tmp_path / "browser" / "projects" / f"project-{older.id}").resolve())
    assert latest_session.id != older_session.id


def test_project_can_be_renamed_and_deleted_without_orphaned_records():
    db = _session()
    project = Project(name="待管理项目", industry="装修")
    other = Project(name="保留项目", industry="教育")
    db.add_all([project, other])
    db.flush()

    keyword = Keyword(project_id=project.id, keyword="长沙装修", category="地域词")
    video = Video(project_id=project.id, platform_video_id="video-1", title="真实视频", keyword="长沙装修")
    comment = Comment(project_id=project.id, video_id=video.id, platform_comment_id="comment-1", content="120平多少钱？", content_hash="hash-1")
    lead = Lead(project_id=project.id, nickname="真实用户")
    task = ScanTask(project_id=project.id, name="真实扫描")
    db.add_all([keyword, video, lead, task])
    db.flush()
    comment.video_id = video.id
    db.add(comment)
    db.flush()
    db.add_all([
        CommentReply(project_id=project.id, comment_id=comment.id, reply_text="待人工确认"),
        LeadComment(lead_id=lead.id, comment_id=comment.id),
        LeadSource(lead_id=lead.id, video_id=video.id),
        LeadEvent(lead_id=lead.id, score=80),
        FollowTask(lead_id=lead.id, project_id=project.id, content="人工跟进", deadline=now_utc()),
        TaskStep(task_id=task.id, name="抓取评论"),
        TaskEvent(task_id=task.id, project_id=project.id, event_type="started", message="开始"),
        TaskCheckpoint(task_id=task.id),
        TaskReport(task_id=task.id),
        TaskArtifact(task_id=task.id, entity_type="video", entity_id=video.id, change_type="created"),
        AgentRun(project_id=project.id, task_id=task.id, agent="LeadJudgeAgent", prompt_version="v1", input_hash="input-hash"),
        BrowserSession(project_id=project.id, profile_path="profile-for-delete"),
    ])
    db.commit()

    renamed = main.update_project(project.id, main.ProjectUpdate(name="已重命名项目"), db)
    assert renamed.name == "已重命名项目"

    result = main.delete_project(project.id, db)
    assert result == {"id": project.id, "deleted": True}
    assert db.get(Project, project.id) is None
    assert db.get(Project, other.id) is not None
    for model in (Keyword, Video, Comment, CommentReply, Lead, FollowTask, ScanTask, TaskStep, TaskEvent, TaskCheckpoint, TaskReport, TaskArtifact, AgentRun, BrowserSession):
        assert db.query(model).filter_by(project_id=project.id).count() == 0 if "project_id" in model.__table__.columns else db.query(model).count() == 0
