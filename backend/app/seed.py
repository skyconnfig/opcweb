from sqlalchemy import inspect

from app.core.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Setting
from app.settings_store import PREFIX, encrypt_secret


def init_database():
    """Create the schema and perform safe migrations required at startup.

    Business data is intentionally not created here. Projects, keywords, videos,
    comments, leads, and personas must come from user actions and real providers.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    _migrate_secrets()
    _ensure_runtime_provider()


def _ensure_runtime_provider():
    """Migrate legacy provider selection without creating business data."""
    with SessionLocal() as db:
        item = db.get(Setting, "content_provider")
        if item is None:
            db.add(Setting(key="content_provider", value="douyin-playwright"))
        elif item.value not in {"douyin-playwright", "Douyin Playwright", "douyin-comments-crawler", "Douyin Comments Crawler"}:
            item.value = "douyin-playwright"
        db.commit()


def _ensure_schema():
    """Add fields introduced after the first SQLite database was created."""
    additions = {
        "videos": {
            "industry_relevance_score": "FLOAT DEFAULT 0",
            "commercial_relevance_score": "FLOAT DEFAULT 0",
            "lead_opportunity_score": "FLOAT DEFAULT 0",
        },
        "comments": {"parent_comment_id": "VARCHAR(120) DEFAULT ''", "id_source": "VARCHAR(30) DEFAULT 'dom_attribute'", "is_reply": "BOOLEAN DEFAULT FALSE", "like_count": "INTEGER DEFAULT 0", "comment_url": "VARCHAR(500) DEFAULT ''"},
        "agent_runs": {
            "model": "VARCHAR(120) DEFAULT ''",
            "input_text": "TEXT DEFAULT ''",
            "error": "TEXT DEFAULT ''",
        },
        "leads": {"time_requirement": "VARCHAR(120) DEFAULT ''", "confidence": "FLOAT DEFAULT 0"},
        "scan_tasks": {"full": "BOOLEAN DEFAULT FALSE"},
        "comment_replies": {
            "sending_started_at": "DATETIME",
            "send_lease_expires_at": "DATETIME",
            "platform_reply_id": "VARCHAR(120) DEFAULT ''",
            "verification_attempt_count": "INTEGER DEFAULT 0",
            "last_verification_at": "DATETIME",
            "verification_due_at": "DATETIME",
            "verification_error_code": "VARCHAR(80) DEFAULT ''",
            "verification_error_message": "TEXT DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column not in existing:
                    connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def _migrate_secrets():
    settings = get_settings()
    if not settings.settings_encryption_key:
        return
    with SessionLocal() as db:
        item = db.get(Setting, "llm_api_key")
        if item and item.value and not item.value.startswith(PREFIX):
            item.value = encrypt_secret(item.value, settings)
            db.commit()
