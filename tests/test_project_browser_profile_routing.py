from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.core.config import Settings
from app.db import Base
from app.models import BrowserSession, Project, now_utc


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
