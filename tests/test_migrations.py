from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _current_head() -> str:
    return ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini"))).get_current_head()


def test_fresh_sqlite_upgrade_reaches_current_head(tmp_path):
    database = tmp_path / "fresh.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        comment_columns = {row[1] for row in connection.execute("PRAGMA table_info(comments)")}
        video_columns = {row[1] for row in connection.execute("PRAGMA table_info(videos)")}
        agent_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        lead_columns = {row[1] for row in connection.execute("PRAGMA table_info(leads)")}
        reply_columns = {row[1] for row in connection.execute("PRAGMA table_info(comment_replies)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert revision == _current_head()
    assert "task_id" in video_columns
    assert "task_id" in comment_columns
    assert "task_id" in agent_run_columns
    assert "task_artifacts" in tables
    assert "comment_url" in comment_columns
    assert {"confidence", "time_requirement"} <= lead_columns
    assert {"knowledge_entries", "comment_replies", "browser_profiles"} <= tables
    assert {"sending_started_at", "send_lease_expires_at", "platform_reply_id", "verification_attempt_count"} <= reply_columns


def test_legacy_sqlite_upgrade_adds_reply_recovery_columns(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY);
            CREATE TABLE comments (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                platform_comment_id TEXT NOT NULL,
                platform_user_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at_platform DATETIME,
                coverage_status TEXT NOT NULL
            );
            CREATE TABLE comment_replies (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                comment_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                reply_text TEXT NOT NULL,
                reply_source TEXT NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                generated_at DATETIME,
                approved_at DATETIME,
                sent_at DATETIME,
                verified_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            """
        )

    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    with sqlite3.connect(database) as connection:
        reply_columns = {row[1] for row in connection.execute("PRAGMA table_info(comment_replies)")}
        comment_columns = {row[1] for row in connection.execute("PRAGMA table_info(comments)")}
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert revision == _current_head()
    assert "comment_url" in comment_columns
    assert {"sending_started_at", "send_lease_expires_at", "verification_error_message"} <= reply_columns
