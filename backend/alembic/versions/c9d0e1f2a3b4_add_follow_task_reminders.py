"""add durable overdue follow-task reminders

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("follow_tasks", sa.Column("overdue_at", sa.DateTime(), nullable=True))
    op.add_column("follow_tasks", sa.Column("reminded_at", sa.DateTime(), nullable=True))
    op.add_column("follow_tasks", sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_follow_tasks_overdue_at", "follow_tasks", ["overdue_at"], unique=False)
    op.create_index("ix_follow_tasks_reminded_at", "follow_tasks", ["reminded_at"], unique=False)

    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("follow_task_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_notification_events_project_id_projects"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name="fk_notification_events_lead_id_leads"),
        sa.ForeignKeyConstraint(["follow_task_id"], ["follow_tasks.id"], name="fk_notification_events_follow_task_id_follow_tasks"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("follow_task_id", "event_type", name="uq_notification_event_follow_task_type"),
    )
    op.create_index("ix_notification_events_project_id", "notification_events", ["project_id"], unique=False)
    op.create_index("ix_notification_events_lead_id", "notification_events", ["lead_id"], unique=False)
    op.create_index("ix_notification_events_follow_task_id", "notification_events", ["follow_task_id"], unique=False)
    op.create_index("ix_notification_events_project_created", "notification_events", ["project_id", "created_at"], unique=False)
    op.create_index("ix_notification_events_project_read", "notification_events", ["project_id", "read_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_events_project_read", table_name="notification_events")
    op.drop_index("ix_notification_events_project_created", table_name="notification_events")
    op.drop_index("ix_notification_events_follow_task_id", table_name="notification_events")
    op.drop_index("ix_notification_events_lead_id", table_name="notification_events")
    op.drop_index("ix_notification_events_project_id", table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_index("ix_follow_tasks_reminded_at", table_name="follow_tasks")
    op.drop_index("ix_follow_tasks_overdue_at", table_name="follow_tasks")
    op.drop_column("follow_tasks", "reminder_count")
    op.drop_column("follow_tasks", "reminded_at")
    op.drop_column("follow_tasks", "overdue_at")
