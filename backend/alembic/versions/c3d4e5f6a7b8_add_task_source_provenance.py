"""add scan task provenance for videos, comments, and agent runs

Revision ID: c3d4e5f6a7b8
Revises: b9c0d1e2f3a4
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable is intentional: records written before task provenance was
    # introduced must remain readable and project-level agent calls do not
    # belong to a scan task.
    # SQLite cannot ALTER TABLE to add a foreign key, so batch mode uses its
    # supported copy-and-move strategy while remaining a regular ALTER on
    # databases that support it natively.
    for table in ("videos", "comments", "agent_runs"):
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.add_column(sa.Column("task_id", sa.Integer(), nullable=True))
            batch_op.create_index(f"ix_{table}_task_id", ["task_id"], unique=False)
            batch_op.create_foreign_key(
                f"fk_{table}_task_id_scan_tasks", "scan_tasks", ["task_id"], ["id"]
            )

    op.create_table(
        "task_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["scan_tasks.id"], name="fk_task_artifacts_task_id_scan_tasks"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "entity_type", "entity_id", name="uq_task_artifact_entity"),
    )
    op.create_index("ix_task_artifacts_task_id", "task_artifacts", ["task_id"], unique=False)
    op.create_index("ix_task_artifacts_task_entity", "task_artifacts", ["task_id", "entity_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_task_artifacts_task_entity", table_name="task_artifacts")
    op.drop_index("ix_task_artifacts_task_id", table_name="task_artifacts")
    op.drop_table("task_artifacts")

    for table in ("agent_runs", "comments", "videos"):
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_constraint(f"fk_{table}_task_id_scan_tasks", type_="foreignkey")
            batch_op.drop_index(f"ix_{table}_task_id")
            batch_op.drop_column("task_id")
