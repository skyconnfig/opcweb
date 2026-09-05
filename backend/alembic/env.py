from logging.config import fileConfig

from alembic.script import ScriptDirectory
from alembic import context
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, pool, text

from app.db import Base
from app.core.config import get_settings
import app.models  # noqa: F401

config = context.config
if config.config_file_name and config.get_section("loggers"):
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _is_legacy_database(connection) -> bool:
    """Return whether this is a pre-Alembic SQLite database.

    Early local checkouts created tables with ``Base.metadata.create_all`` and
    therefore have no meaningful Alembic revision.  Running the initial
    revision against that database would try to create ``projects`` again.
    """

    if connection.dialect.name != "sqlite":
        return False
    inspector = inspect(connection)
    if not inspector.has_table("projects"):
        return False
    if not inspector.has_table("alembic_version"):
        return True
    return connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one() == 0


def _ensure_legacy_columns(connection) -> None:
    """Add columns that were introduced after the original local schema."""

    additions = {
        "videos": {
            "industry_relevance_score": "FLOAT DEFAULT 0",
            "commercial_relevance_score": "FLOAT DEFAULT 0",
            "lead_opportunity_score": "FLOAT DEFAULT 0",
        },
        "comments": {
            "parent_comment_id": "VARCHAR(120) DEFAULT ''",
            "id_source": "VARCHAR(30) DEFAULT 'dom_attribute'",
            "is_reply": "BOOLEAN DEFAULT FALSE",
            "like_count": "INTEGER DEFAULT 0",
            "comment_url": "VARCHAR(500) DEFAULT ''",
        },
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
    inspector = inspect(connection)
    for table, columns in additions.items():
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
        inspector = inspect(connection)


def _stamp_legacy_database(connection) -> None:
    """Bring a legacy local database to the current model before stamping.

    ``create_all`` is intentionally used only for this compatibility bridge;
    all fresh databases still run the normal Alembic revision chain.
    """

    Base.metadata.create_all(bind=connection)
    _ensure_legacy_columns(connection)


def _stamp_current_head(connection) -> None:
    """Record the head revision after the legacy schema is reconciled."""

    version_table = Table(
        "alembic_version",
        MetaData(),
        Column("version_num", String(length=32), primary_key=True),
    )
    version_table.create(connection, checkfirst=True)
    connection.execute(version_table.delete())
    connection.execute(version_table.insert().values(version_num=ScriptDirectory.from_config(config).get_current_head()))


def run_migrations_offline():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        legacy_database = _is_legacy_database(connection)
        if legacy_database:
            _stamp_legacy_database(connection)
            _stamp_current_head(connection)
            connection.commit()
            return
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        # SQLite does not always infer transactional DDL from the dialect.
        # Commit the Alembic version row explicitly so a fresh database is at
        # the requested revision after the first `upgrade head` invocation.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
