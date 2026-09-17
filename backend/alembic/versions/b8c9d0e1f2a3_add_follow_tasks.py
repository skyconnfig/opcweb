"""add human-owned lead follow-up tasks

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("follow_note", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "follow_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_follow_tasks_lead_id_leads"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_follow_tasks_project_id_projects"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_follow_tasks_lead_id", "follow_tasks", ["lead_id"], unique=False)
    op.create_index("ix_follow_tasks_project_id", "follow_tasks", ["project_id"], unique=False)
    op.create_index("ix_follow_tasks_deadline", "follow_tasks", ["deadline"], unique=False)
    op.create_index("ix_follow_tasks_status", "follow_tasks", ["status"], unique=False)
    op.create_index(
        "ix_follow_tasks_project_status_deadline",
        "follow_tasks",
        ["project_id", "status", "deadline"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_follow_tasks_project_status_deadline", table_name="follow_tasks")
    op.drop_index("ix_follow_tasks_status", table_name="follow_tasks")
    op.drop_index("ix_follow_tasks_deadline", table_name="follow_tasks")
    op.drop_index("ix_follow_tasks_project_id", table_name="follow_tasks")
    op.drop_index("ix_follow_tasks_lead_id", table_name="follow_tasks")
    op.drop_table("follow_tasks")
    op.drop_column("leads", "follow_note")
