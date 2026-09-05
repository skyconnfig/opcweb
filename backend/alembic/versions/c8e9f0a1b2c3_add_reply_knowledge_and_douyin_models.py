"""add reply, knowledge, and douyin browser models

Revision ID: c8e9f0a1b2c3
Revises: 7f1a2c9d4e6b
Create Date: 2026-09-04 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e9f0a1b2c3"
down_revision: Union[str, None] = "7f1a2c9d4e6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_entries_project_id"), "knowledge_entries", ["project_id"], unique=False)
    op.create_index(op.f("ix_knowledge_entries_enabled"), "knowledge_entries", ["enabled"], unique=False)
    op.create_index("ix_knowledge_entries_project_enabled", "knowledge_entries", ["project_id", "enabled"], unique=False)

    op.create_table(
        "douyin_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("profile_dir", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="LOGGED_OUT"),
        sa.Column("nickname", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("douyin_user_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_douyin_accounts_status"), "douyin_accounts", ["status"], unique=False)
    op.create_index(op.f("ix_douyin_accounts_douyin_user_id"), "douyin_accounts", ["douyin_user_id"], unique=False)

    op.create_table(
        "browser_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("profile_dir", sa.String(length=500), nullable=False),
        sa.Column("browser_channel", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("headless", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="INACTIVE"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["douyin_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "profile_dir", name="uq_browser_profile_account_dir"),
    )
    op.create_index(op.f("ix_browser_profiles_account_id"), "browser_profiles", ["account_id"], unique=False)
    op.create_index(op.f("ix_browser_profiles_status"), "browser_profiles", ["status"], unique=False)
    op.create_index("ix_browser_profiles_account_status", "browser_profiles", ["account_id", "status"], unique=False)

    op.create_table(
        "reply_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_reply_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minimum_confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("minimum_lead_score", sa.Float(), nullable=False, server_default="70"),
        sa.Column("allowed_intents", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("blocked_intents", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("max_replies_per_hour", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_replies_per_day", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("minimum_interval_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("auto_reply_own_content_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_reply_policy_project"),
    )
    op.create_index(op.f("ix_reply_policies_project_id"), "reply_policies", ["project_id"], unique=False)

    op.create_table(
        "comment_replies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False, server_default="douyin"),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("reply_source", sa.String(length=20), nullable=False, server_default="AI"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("error_code", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_comment_replies_project_id"), "comment_replies", ["project_id"], unique=False)
    op.create_index(op.f("ix_comment_replies_comment_id"), "comment_replies", ["comment_id"], unique=False)
    op.create_index(op.f("ix_comment_replies_status"), "comment_replies", ["status"], unique=False)
    op.create_index("ix_comment_replies_project_status", "comment_replies", ["project_id", "status"], unique=False)
    op.create_index("ix_comment_replies_comment_status", "comment_replies", ["comment_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_comment_replies_comment_status", table_name="comment_replies")
    op.drop_index("ix_comment_replies_project_status", table_name="comment_replies")
    op.drop_index(op.f("ix_comment_replies_status"), table_name="comment_replies")
    op.drop_index(op.f("ix_comment_replies_comment_id"), table_name="comment_replies")
    op.drop_index(op.f("ix_comment_replies_project_id"), table_name="comment_replies")
    op.drop_table("comment_replies")

    op.drop_index(op.f("ix_reply_policies_project_id"), table_name="reply_policies")
    op.drop_table("reply_policies")

    op.drop_index("ix_browser_profiles_account_status", table_name="browser_profiles")
    op.drop_index(op.f("ix_browser_profiles_status"), table_name="browser_profiles")
    op.drop_index(op.f("ix_browser_profiles_account_id"), table_name="browser_profiles")
    op.drop_table("browser_profiles")

    op.drop_index(op.f("ix_douyin_accounts_douyin_user_id"), table_name="douyin_accounts")
    op.drop_index(op.f("ix_douyin_accounts_status"), table_name="douyin_accounts")
    op.drop_table("douyin_accounts")

    op.drop_index("ix_knowledge_entries_project_enabled", table_name="knowledge_entries")
    op.drop_index(op.f("ix_knowledge_entries_enabled"), table_name="knowledge_entries")
    op.drop_index(op.f("ix_knowledge_entries_project_id"), table_name="knowledge_entries")
    op.drop_table("knowledge_entries")
