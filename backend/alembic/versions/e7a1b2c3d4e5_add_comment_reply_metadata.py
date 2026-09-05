"""store comment reply and like metadata

Revision ID: e7a1b2c3d4e5
Revises: d4f6a8b9c0d1
Create Date: 2026-09-04 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a1b2c3d4e5"
down_revision: Union[str, None] = "d4f6a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("is_reply", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("comments", sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("comments", "like_count")
    op.drop_column("comments", "is_reply")
