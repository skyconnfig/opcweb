"""add reply send leases, verification audit fields, and claim guard

Revision ID: a2c4e6f8b0d2
Revises: f1b2c3d4e5f6
Create Date: 2026-09-04 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2c4e6f8b0d2"
down_revision: Union[str, None] = "f1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _deduplicate_sending_rows(connection) -> None:
    """Keep the oldest claim before creating the partial unique index."""

    rows = connection.execute(
        sa.text(
            "SELECT id, comment_id FROM comment_replies "
            "WHERE status = 'SENDING' ORDER BY comment_id, id"
        )
    ).all()
    retained: set[int] = set()
    for reply_id, comment_id in rows:
        if comment_id not in retained:
            retained.add(comment_id)
            continue
        connection.execute(
            sa.text(
                "UPDATE comment_replies SET status = 'FAILED', "
                "error_code = 'SENDING_DUPLICATE', "
                "error_message = '迁移时发现重复发送占用，已安全释放该记录' "
                "WHERE id = :reply_id"
            ),
            {"reply_id": reply_id},
        )


def upgrade() -> None:
    op.add_column("comment_replies", sa.Column("sending_started_at", sa.DateTime(), nullable=True))
    op.add_column("comment_replies", sa.Column("send_lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("comment_replies", sa.Column("platform_reply_id", sa.String(length=120), nullable=False, server_default=""))
    op.add_column("comment_replies", sa.Column("verification_attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("comment_replies", sa.Column("last_verification_at", sa.DateTime(), nullable=True))
    op.add_column("comment_replies", sa.Column("verification_due_at", sa.DateTime(), nullable=True))
    op.add_column("comment_replies", sa.Column("verification_error_code", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("comment_replies", sa.Column("verification_error_message", sa.Text(), nullable=False, server_default=""))

    connection = op.get_bind()
    _deduplicate_sending_rows(connection)

    index_name = "uq_comment_replies_comment_sending"
    where = sa.text("status = 'SENDING'")
    if connection.dialect.name == "sqlite":
        op.create_index(index_name, "comment_replies", ["comment_id"], unique=True, sqlite_where=where)
    elif connection.dialect.name == "postgresql":
        op.create_index(index_name, "comment_replies", ["comment_id"], unique=True, postgresql_where=where)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name in {"sqlite", "postgresql"}:
        op.drop_index("uq_comment_replies_comment_sending", table_name="comment_replies")
    op.drop_column("comment_replies", "verification_error_message")
    op.drop_column("comment_replies", "verification_error_code")
    op.drop_column("comment_replies", "verification_due_at")
    op.drop_column("comment_replies", "last_verification_at")
    op.drop_column("comment_replies", "verification_attempt_count")
    op.drop_column("comment_replies", "platform_reply_id")
    op.drop_column("comment_replies", "send_lease_expires_at")
    op.drop_column("comment_replies", "sending_started_at")
