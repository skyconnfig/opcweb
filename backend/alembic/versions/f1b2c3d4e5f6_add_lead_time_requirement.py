"""add lead time requirement

Revision ID: f1b2c3d4e5f6
Revises: e7a1b2c3d4e5
Create Date: 2026-09-04 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "f1b2c3d4e5f6"
down_revision: Union[str, None] = "e7a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("leads", sa.Column("time_requirement", sa.String(length=120), nullable=False, server_default=""))
        op.add_column("leads", sa.Column("confidence", sa.Float(), nullable=False, server_default="0"))
        return
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("leads")}
    if "time_requirement" not in existing:
        op.add_column("leads", sa.Column("time_requirement", sa.String(length=120), nullable=False, server_default=""))
    if "confidence" not in existing:
        op.add_column("leads", sa.Column("confidence", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("leads", "confidence")
    op.drop_column("leads", "time_requirement")
