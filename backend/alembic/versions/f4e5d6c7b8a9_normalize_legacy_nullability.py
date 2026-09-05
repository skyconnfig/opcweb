"""normalize nullable columns from legacy local databases

Revision ID: f4e5d6c7b8a9
Revises: c3d4e5f6a7b8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4e5d6c7b8a9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Databases created by the pre-Alembic bootstrap allowed these fields to
    # be NULL. Preserve those records while bringing them to the current
    # model contract before applying NOT NULL constraints.
    replacements = {
        "agent_runs": {"model": "", "input_text": "", "error": ""},
        "comments": {"id_source": "dom_attribute", "parent_comment_id": "", "is_reply": False, "like_count": 0},
        "scan_tasks": {"full": False},
        "videos": {"industry_relevance_score": 0, "commercial_relevance_score": 0, "lead_opportunity_score": 0},
    }
    for table, columns in replacements.items():
        for column, value in columns.items():
            op.execute(sa.text(f'UPDATE "{table}" SET "{column}" = :value WHERE "{column}" IS NULL').bindparams(value=value))

    definitions = {
        "agent_runs": {
            "model": sa.String(length=120),
            "input_text": sa.Text(),
            "error": sa.Text(),
        },
        "comments": {
            "id_source": sa.String(length=30),
            "parent_comment_id": sa.String(length=120),
            "is_reply": sa.Boolean(),
            "like_count": sa.Integer(),
        },
        "scan_tasks": {"full": sa.Boolean()},
        "videos": {
            "industry_relevance_score": sa.Float(),
            "commercial_relevance_score": sa.Float(),
            "lead_opportunity_score": sa.Float(),
        },
    }
    for table, columns in definitions.items():
        with op.batch_alter_table(table, recreate="always") as batch_op:
            for column, column_type in columns.items():
                batch_op.alter_column(column, existing_type=column_type, nullable=False)


def downgrade() -> None:
    definitions = {
        "agent_runs": {
            "model": sa.String(length=120),
            "input_text": sa.Text(),
            "error": sa.Text(),
        },
        "comments": {
            "id_source": sa.String(length=30),
            "parent_comment_id": sa.String(length=120),
            "is_reply": sa.Boolean(),
            "like_count": sa.Integer(),
        },
        "scan_tasks": {"full": sa.Boolean()},
        "videos": {
            "industry_relevance_score": sa.Float(),
            "commercial_relevance_score": sa.Float(),
            "lead_opportunity_score": sa.Float(),
        },
    }
    for table, columns in definitions.items():
        with op.batch_alter_table(table, recreate="always") as batch_op:
            for column, column_type in columns.items():
                batch_op.alter_column(column, existing_type=column_type, nullable=True)
