"""persist source URL for each public comment

Revision ID: b9c0d1e2f3a4
Revises: a2c4e6f8b0d2
"""

from alembic import op
import sqlalchemy as sa


revision = "b9c0d1e2f3a4"
down_revision = "a2c4e6f8b0d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("comment_url", sa.String(length=500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("comments", "comment_url")
