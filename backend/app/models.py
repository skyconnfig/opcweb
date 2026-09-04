from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(120), default="")
    service: Mapped[str] = mapped_column(String(300), default="")
    price_range: Mapped[str] = mapped_column(String(120), default="")
    target_customer: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    intelligence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
    keywords: Mapped[list["Keyword"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Persona(Base):
    __tablename__ = "personas"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    identity: Mapped[str] = mapped_column(String(200), default="")
    experience: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    tone: Mapped[str] = mapped_column(String(200), default="专业但不推销")
    strengths: Mapped[str] = mapped_column(Text, default="")
    forbidden_words: Mapped[str] = mapped_column(Text, default="")
    sample_reply: Mapped[str] = mapped_column(Text, default="")


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (Index("ix_keywords_project_opportunity", "project_id", "opportunity_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    keyword: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(60))
    intent_score: Mapped[float] = mapped_column(Float, default=0)
    commercial_score: Mapped[float] = mapped_column(Float, default=0)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(String(40), default="ai")
    reason: Mapped[str] = mapped_column(Text, default="")
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    lead_count: Mapped[int] = mapped_column(Integer, default=0)
    s_count: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    project: Mapped[Project] = relationship(back_populates="keywords")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("project_id", "platform", "platform_video_id", name="uq_video_project_platform_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    platform: Mapped[str] = mapped_column(String(40), default="douyin")
    platform_video_id: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    cover: Mapped[str] = mapped_column(String(500), default="")
    publish_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    collects: Mapped[int] = mapped_column(Integer, default=0)
    keyword: Mapped[str] = mapped_column(String(200), index=True)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    industry_relevance_score: Mapped[float] = mapped_column(Float, default=0)
    commercial_relevance_score: Mapped[float] = mapped_column(Float, default=0)
    lead_opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    level: Mapped[str] = mapped_column(String(2), default="C")
    lead_density: Mapped[float] = mapped_column(Float, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (UniqueConstraint("project_id", "platform", "platform_comment_id", name="uq_comment_project_platform_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    platform: Mapped[str] = mapped_column(String(40), default="douyin")
    platform_comment_id: Mapped[str] = mapped_column(String(120), index=True)
    platform_user_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    nickname: Mapped[str] = mapped_column(String(120), default="")
    profile_url: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_comment_id: Mapped[str] = mapped_column(String(120), default="")
    created_at_platform: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    coverage_status: Mapped[str] = mapped_column(String(20), default="unknown")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_project_score", "project_id", "lead_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    platform: Mapped[str] = mapped_column(String(40), default="douyin")
    platform_user_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    nickname: Mapped[str] = mapped_column(String(120), default="")
    profile_url: Mapped[str] = mapped_column(String(500), default="")
    lead_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    lead_level: Mapped[str] = mapped_column(String(2), default="C", index=True)
    intent_level: Mapped[str] = mapped_column(String(20), default="low")
    need: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    budget: Mapped[str] = mapped_column(String(120), default="")
    purchase_stage: Mapped[str] = mapped_column(String(60), default="unknown")
    pain_point: Mapped[str] = mapped_column(Text, default="")
    buying_signals: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    recommended_action: Mapped[str] = mapped_column(String(80), default="observe")
    status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    persona_advice: Mapped[dict] = mapped_column(JSON, default=dict)


class LeadComment(Base):
    __tablename__ = "lead_comments"
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class LeadSource(Base):
    __tablename__ = "lead_sources"
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), primary_key=True)


class LeadEvent(Base):
    __tablename__ = "lead_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    event_type: Mapped[str] = mapped_column(String(40), default="detected")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ScanTask(Base):
    __tablename__ = "scan_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    full: Mapped[bool] = mapped_column(default=False)
    current_step: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class TaskStep(Base):
    __tablename__ = "task_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("scan_tasks.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("scan_tasks.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoints"
    task_id: Mapped[int] = mapped_column(ForeignKey("scan_tasks.id"), primary_key=True)
    last_keyword_id: Mapped[int] = mapped_column(Integer, default=0)
    last_video_id: Mapped[int] = mapped_column(Integer, default=0)
    last_comment_cursor: Mapped[str] = mapped_column(String(200), default="")
    processed_comment_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class TaskReport(Base):
    __tablename__ = "task_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("scan_tasks.id"), unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agent: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120), default="deterministic-mock")
    prompt_version: Mapped[str] = mapped_column(String(40))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_text: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ProviderRecord(Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="disconnected")
    platform: Mapped[str] = mapped_column(String(40), default="douyin")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    endpoint: Mapped[str] = mapped_column(String(500), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
