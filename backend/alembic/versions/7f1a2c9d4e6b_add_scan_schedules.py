"""add project scan schedules

Revision ID: 7f1a2c9d4e6b
Revises: de5a43a55dc8
Create Date: 2026-09-04 10:25:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f1a2c9d4e6b"
down_revision: Union[str, None] = "de5a43a55dc8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("full", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_scan_schedule_project"),
    )
    op.create_index(op.f("ix_scan_schedules_project_id"), "scan_schedules", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scan_schedules_project_id"), table_name="scan_schedules")
    op.drop_table("scan_schedules")
