"""add comment id provenance

Revision ID: d4f6a8b9c0d1
Revises: c8e9f0a1b2c3
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f6a8b9c0d1"
down_revision = "c8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("id_source", sa.String(length=30), nullable=False, server_default="dom_attribute"))


def downgrade() -> None:
    op.drop_column("comments", "id_source")
