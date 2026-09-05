"""Safety checks for real reply sends.

The policy is a business safety boundary, not a platform-evasion feature.
It is deliberately independent from Playwright so it can be tested without
opening a browser or sending an external message.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.orm import Session

from app.models import Comment, CommentReply, Lead, ReplyPolicy, now_utc


_SENT_STATUSES = ("SENDING", "SENT", "SENT_UNVERIFIED", "VERIFIED")
DEFAULT_SENDING_LEASE_SECONDS = 5 * 60


def policy_for(db: Session, project_id: int) -> ReplyPolicy:
    policy = db.scalar(select(ReplyPolicy).where(ReplyPolicy.project_id == project_id))
    if policy is not None:
        return policy
    # A missing row means the safe default: manual, confirmed sends are
    # allowed, automatic sends are disabled, and conservative rate limits
    # apply.  Do not create a row as a side effect of a failed send attempt.
    return ReplyPolicy(
        project_id=project_id,
        enabled=True,
        auto_reply_enabled=False,
        minimum_confidence=0.8,
        minimum_lead_score=70,
        allowed_intents=[],
        blocked_intents=[],
        max_replies_per_hour=10,
        max_replies_per_day=50,
        minimum_interval_seconds=30,
        auto_reply_own_content_only=False,
    )


def recover_stale_sending(
    db: Session,
    *,
    now: datetime | None = None,
    lease_timeout_seconds: int = DEFAULT_SENDING_LEASE_SECONDS,
    comment_id: int | None = None,
) -> int:
    """Move expired ``SENDING`` rows to ``FAILED`` in one DB statement.

    Rows created before the lease columns existed use ``updated_at`` as a
    compatibility fallback.  The caller owns the surrounding transaction;
    this function flushes the update but deliberately does not commit it.
    That makes it safe to use alongside a send claim or a larger unit of
    work, while still making the transition durable when the caller commits.
    """

    if lease_timeout_seconds <= 0:
        raise ValueError("lease_timeout_seconds must be positive")

    checked_at = now or now_utc()
    legacy_cutoff = checked_at - timedelta(seconds=lease_timeout_seconds)
    expired_lease = and_(
        CommentReply.send_lease_expires_at.is_not(None),
        CommentReply.send_lease_expires_at <= checked_at,
    )
    legacy_expired = and_(
        CommentReply.send_lease_expires_at.is_(None),
        CommentReply.updated_at <= legacy_cutoff,
    )
    conditions = [
        CommentReply.status == "SENDING",
        or_(expired_lease, legacy_expired),
    ]
    if comment_id is not None:
        conditions.append(CommentReply.comment_id == comment_id)

    result = db.execute(
        update(CommentReply)
        .where(*conditions)
        .values(
            status="FAILED",
            error_code="SENDING_EXPIRED",
            error_message="发送租约已过期，未确认抖音结果；请人工审核后重试",
            send_lease_expires_at=None,
        )
    )
    return int(result.rowcount or 0)


def record_reply_verification(
    db: Session,
    reply_id: int,
    *,
    verified: bool,
    checked_at: datetime | None = None,
    platform_reply_id: str | None = None,
    error_code: str = "",
    error_message: str = "",
) -> CommentReply:
    """Record a later DOM re-check for a ``SENT_UNVERIFIED`` reply.

    A failed re-check intentionally keeps the row in ``SENT_UNVERIFIED`` so
    a transient page/DOM miss cannot make a second send look safe.  Only an
    explicit positive check transitions it to ``VERIFIED``.
    """

    reply = db.get(CommentReply, reply_id)
    if reply is None:
        raise LookupError(f"reply {reply_id} does not exist")
    if reply.status not in {"SENT", "SENT_UNVERIFIED"}:
        raise ValueError("only SENT or SENT_UNVERIFIED replies can be re-verified")

    checked = checked_at or now_utc()
    reply.verification_attempt_count = int(reply.verification_attempt_count or 0) + 1
    reply.last_verification_at = checked
    if platform_reply_id is not None:
        reply.platform_reply_id = platform_reply_id

    if verified:
        reply.status = "VERIFIED"
        reply.verified_at = checked
        reply.verification_error_code = ""
        reply.verification_error_message = ""
    else:
        reply.verification_error_code = error_code or "REPLY_NOT_VERIFIED"
        reply.verification_error_message = error_message or "未在抖音评论 DOM 中确认该回复"

    db.flush()
    return reply


def enforce_send_policy(
    db: Session,
    comment: Comment,
    *,
    lead: Lead | None = None,
    automatic: bool = False,
) -> ReplyPolicy:
    policy = policy_for(db, comment.project_id)
    if not policy.enabled:
        raise _policy_error("REPLY_POLICY_DISABLED", "当前项目的回复策略已禁用")
    if automatic and not policy.auto_reply_enabled:
        raise _policy_error("AUTO_REPLY_DISABLED", "当前项目未启用自动回复")

    if automatic:
        if lead is None:
            raise _policy_error("AUTO_REPLY_LEAD_REQUIRED", "自动回复必须先有已保存的潜客判断")
        if float(lead.confidence or 0) < float(policy.minimum_confidence):
            raise _policy_error("AUTO_REPLY_CONFIDENCE_TOO_LOW", "潜客判断置信度未达到自动回复阈值")
        if float(lead.lead_score or 0) < float(policy.minimum_lead_score):
            raise _policy_error("AUTO_REPLY_SCORE_TOO_LOW", "潜客评分未达到自动回复阈值")
        intent = str(lead.intent_level or "").strip().lower()
        allowed = {str(value).strip().lower() for value in (policy.allowed_intents or []) if str(value).strip()}
        blocked = {str(value).strip().lower() for value in (policy.blocked_intents or []) if str(value).strip()}
        if blocked and intent in blocked:
            raise _policy_error("AUTO_REPLY_INTENT_BLOCKED", "该意图已被回复策略阻止")
        if allowed and intent not in allowed:
            raise _policy_error("AUTO_REPLY_INTENT_NOT_ALLOWED", "该意图不在自动回复允许范围内")
        if policy.auto_reply_own_content_only:
            # The current data model does not prove content ownership.  Never
            # guess based on a nickname or a URL; fail closed until an
            # explicit ownership relation is available.
            raise _policy_error("AUTO_REPLY_OWN_CONTENT_UNVERIFIED", "仅回复自有内容的规则当前无法被真实数据证明")

    sent = db.scalars(
        select(CommentReply)
        .where(CommentReply.project_id == comment.project_id, CommentReply.status.in_(_SENT_STATUSES))
        .order_by(desc(CommentReply.sent_at))
    ).all()
    now = now_utc()
    sent_times = [_as_utc(row.sent_at) for row in sent if row.sent_at is not None]
    if len([value for value in sent_times if value >= now - timedelta(hours=1)]) >= policy.max_replies_per_hour:
        raise _policy_error("REPLY_HOURLY_LIMIT", "已达到项目每小时回复上限")
    if len([value for value in sent_times if value >= now - timedelta(days=1)]) >= policy.max_replies_per_day:
        raise _policy_error("REPLY_DAILY_LIMIT", "已达到项目每日回复上限")
    if sent_times and sent_times[0] > now - timedelta(seconds=policy.minimum_interval_seconds):
        raise _policy_error("REPLY_INTERVAL_LIMIT", "距离上一条真实回复的安全间隔不足")
    return policy


def _policy_error(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"code": code, "message": message, "detail": {}})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
