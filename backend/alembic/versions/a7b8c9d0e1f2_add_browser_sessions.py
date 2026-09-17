"""add durable browser session status records

Revision ID: a7b8c9d0e1f2
Revises: f4e5d6c7b8a9
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f4e5d6c7b8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("profile_path", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="LOGIN_REQUIRED"),
        sa.Column("last_check_time", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_browser_sessions_project_id_projects"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "profile_path", name="uq_browser_session_project_profile"),
    )
    op.create_index("ix_browser_sessions_project_id", "browser_sessions", ["project_id"], unique=False)
    op.create_index("ix_browser_sessions_status", "browser_sessions", ["status"], unique=False)
    op.create_index("ix_browser_sessions_project_status", "browser_sessions", ["project_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_browser_sessions_project_status", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_status", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_project_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
