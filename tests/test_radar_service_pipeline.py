from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.llm import BaseLLMProvider, LLMCall
from app.agents.reply_agent import ReplyDecision
from app.db import Base
from app.models import AgentRun, Comment, CommentReply, Keyword, KnowledgeEntry, Lead, LeadComment, LeadEvent, Persona, Project, ReplyPolicy, ScanTask, TaskArtifact, TaskCheckpoint, TaskReport, TaskStep, Video
from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO
from app.providers.douyin.dto import ReplyResult, ReplyStatus
from app.services import radar_service
from app.services.radar_service import RadarService, _keywords_after_checkpoint, _upsert_lead
from app.tasks.queue import enqueue_scan


class ScriptedProvider(BaseContentProvider):
    def __init__(self, pages: dict[str | None, CommentScanResult]):
        self.pages = pages
        self.cursors: list[str | None] = []
        self.video = VideoDTO(
            "douyin",
            "video-1",
            "长沙装修预算",
            "长沙本地装修经验分享",
            "装修老李",
            "https://www.douyin.com/video/video-1",
            "https://example.test/cover.jpg",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            1200,
            328,
            80,
            100,
            "长沙装修",
        )

    async def health_check(self):
        return ProviderHealth("connected", "test provider")

    async def search_videos(self, keyword: str, limit: int):
        return [self.video]

    async def get_video(self, video_id: str):
        return self.video

    async def get_comments(self, video_id: str, cursor: str | None = None):
        self.cursors.append(cursor)
        return self.pages[cursor]


class AutoReplyProvider(ScriptedProvider):
    capabilities = {"reply_comment": True}

    def __init__(self, pages: dict[str | None, CommentScanResult]):
        super().__init__(pages)
        self.reply_calls: list[tuple[str, str, str]] = []

    async def reply_comment(self, video_url, comment, text):
        self.reply_calls.append((video_url, comment.comment_id, text))
        return ReplyResult(ReplyStatus.VERIFIED, "douyin", video_url, comment.comment_id, text, True)


class RecordingTextLLM(BaseLLMProvider):
    configured = True
    model = "offline-text-test"

    def __init__(self, fail_calls: set[int] | None = None):
        super().__init__()
        self.calls = 0
        self.users: list[str] = []
        self.fail_calls = fail_calls or set()

    async def structured_output(self, system: str, user: str, schema: dict):
        self.calls += 1
        self.users.append(user)
        if self.calls in self.fail_calls:
            # Deliberately do not populate last_call.  This exercises the
            # service's failure bookkeeping when a custom provider fails
            # before it can emit telemetry.
            raise RuntimeError("offline text provider failed")
        self.last_call = LLMCall(self.model, user, {}, tokens=11, latency_ms=2)
        return {
            "is_lead": True,
            "confidence": 0.91,
            "lead_score": 88,
            "intent_level": "high",
            "need": "旧房翻新",
            "location": "长沙",
            "budget": "10万",
            "time_requirement": "年底",
            "purchase_stage": "准备咨询",
            "pain_point": "担心增项",
            "buying_signals": ["询价", "明确时间"],
            "summary": "用户有明确装修计划",
            "reason": "评论包含地区、预算和时间",
            "recommended_action": "人工跟进",
            "should_reply": True,
        }


async def _noop(*args, **kwargs):
    return None


def test_checkpoint_resume_uses_sorted_keyword_position_not_database_id():
    first = Keyword(id=20, keyword="高机会词")
    second = Keyword(id=3, keyword="低 id 但后续词")

    assert _keywords_after_checkpoint([first, second], first.id) == [second]


def _session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(radar_service, "SessionLocal", sessions)
    return sessions


def _task(sessions):
    db = sessions()
    project = Project(
        name="长沙装修",
        industry="装修",
        location="长沙",
        service="旧房翻新",
        target_customer="准备装修的长沙业主",
        price_range="5万-30万",
        description="提供设计与施工",
    )
    db.add(project)
    db.flush()
    db.add(Keyword(project_id=project.id, keyword="长沙装修", category="核心词", enabled=True))
    db.commit()
    task = enqueue_scan(db, project.id, full=True)
    db.close()
    return task.id, project.id


def _comment(comment_id: str, user_id: str, content: str, parent: str = ""):
    return CommentDTO("douyin", comment_id, user_id, "客户", "", content, parent_comment_id=parent)


def _service(provider, llm):
    service = RadarService(provider, llm)
    service._step = _noop
    service.emit = _noop
    return service


@pytest.mark.asyncio
async def test_pipeline_persists_text_comments_judges_candidates_and_reports_real_counts(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    comments = [
        _comment("c-noise", "noise-user", "哈哈哈"),
        _comment("c1", "user-1", "长沙有没有？"),
        _comment("c2", "user-1", "120平大概多少钱？"),
        _comment("c3", "user-1", "年底准备装。"),
    ]
    provider = ScriptedProvider({None: CommentScanResult(comments, "partial", 999, None, False)})
    llm = RecordingTextLLM()

    await _service(provider, llm).run_task(task_id, full=True)

    with sessions() as db:
        stored_comments = db.scalars(select(Comment).where(Comment.project_id == project_id).order_by(Comment.id)).all()
        leads = db.scalars(select(Lead).where(Lead.project_id == project_id)).all()
        report = db.scalar(select(TaskReport).where(TaskReport.task_id == task_id))
        runs = db.scalars(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.agent == "LeadJudgeAgent")).all()
        relations = db.scalars(select(LeadComment).join(Lead, Lead.id == LeadComment.lead_id)).all()

    assert len(stored_comments) == 4
    assert len(leads) == 1
    assert leads[0].occurrence_count == 3
    assert len(relations) == 3
    assert len(runs) == 3 and all(run.success for run in runs)
    with sessions() as db:
        stored_video = db.scalar(select(Video).where(Video.project_id == project_id))
        artifacts = db.scalars(select(TaskArtifact).where(TaskArtifact.task_id == task_id)).all()
    assert stored_video.task_id == task_id
    assert all(comment.task_id == task_id for comment in stored_comments)
    assert all(run.task_id == task_id for run in runs)
    assert {(item.entity_type, item.change_type) for item in artifacts} >= {
        ("video", "created"),
        ("comment", "created"),
        ("lead", "created"),
        ("agent_run", "created"),
    }
    assert report is not None
    assert report.metrics["videos"] == 1
    assert report.metrics["videos_new"] == 1 and report.metrics["videos_updated"] == 0
    assert report.metrics["comments"] == 4
    assert report.metrics["comments_new"] == 4 and report.metrics["comments_updated"] == 0
    assert report.metrics["comments_received"] == 4
    assert report.metrics["comments_prefiltered"] == 1
    assert report.metrics["comments_judged"] == 3
    assert report.metrics["judgments"] == 3 and report.metrics["judgments_new"] == 3
    assert report.metrics["prefilter_ratio"] == 0.25
    assert report.metrics["coverage_status"] == "partial"
    assert report.metrics["coverage_statuses"] == ["partial"]
    assert report.metrics["leads"] == 1
    assert report.metrics["leads_new"] == 1 and report.metrics["leads_updated"] == 0
    assert report.metrics["s_leads"] == 0
    assert all(text in llm.users[-1] for text in ("长沙有没有？", "120平大概多少钱？", "年底准备装。"))
    assert "https://" not in llm.users[-1]


@pytest.mark.asyncio
async def test_repeated_scan_keeps_history_and_reports_created_vs_updated(monkeypatch):
    sessions = _session(monkeypatch)
    first_task_id, project_id = _task(sessions)
    provider = ScriptedProvider(
        {None: CommentScanResult([_comment("c1", "user-1", "长沙有没有？")], "complete", 1, None, False)}
    )

    await _service(provider, RecordingTextLLM()).run_task(first_task_id, full=True)
    with sessions() as db:
        second_task = enqueue_scan(db, project_id, full=True)
        second_task_id = second_task.id

    await _service(provider, RecordingTextLLM()).run_task(second_task_id, full=True)

    with sessions() as db:
        video = db.scalar(select(Video).where(Video.project_id == project_id))
        comment = db.scalar(select(Comment).where(Comment.project_id == project_id))
        reports = db.scalars(select(TaskReport).where(TaskReport.task_id.in_([first_task_id, second_task_id]))).all()
        artifacts = db.scalars(
            select(TaskArtifact).where(
                TaskArtifact.entity_type == "comment",
                TaskArtifact.entity_id == comment.id,
            ).order_by(TaskArtifact.task_id)
        ).all()

    assert video.task_id == second_task_id
    assert comment.task_id == second_task_id
    assert len(artifacts) == 2
    assert [item.change_type for item in artifacts] == ["created", "updated"]
    assert reports[0].metrics["comments_new"] == 1
    assert reports[1].metrics["comments_new"] == 0
    assert reports[1].metrics["comments_updated"] == 1
    assert reports[1].metrics["videos_new"] == 0
    assert reports[1].metrics["videos_updated"] == 1
    assert reports[1].metrics["leads_new"] == 0
    assert reports[1].metrics["leads_updated"] == 1
    assert reports[1].metrics["judgments"] == 1


@pytest.mark.asyncio
async def test_pipeline_without_llm_creates_no_lead_and_records_failed_agent_run(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = ScriptedProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙多少钱？")], "partial", 1, None, False)})

    await _service(provider, BaseLLMProvider()).run_task(task_id, full=True)

    with sessions() as db:
        task = db.get(ScanTask, task_id)
        comment_count = len(db.scalars(select(Comment).where(Comment.project_id == project_id)).all())
        lead_count = len(db.scalars(select(Lead).where(Lead.project_id == project_id)).all())
        run = db.scalar(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.agent == "LeadJudgeAgent"))

    assert task.status == "failed"
    assert comment_count == 1
    assert lead_count == 0
    assert run is not None and run.success is False and run.error
    assert run.output == {}


@pytest.mark.asyncio
async def test_pipeline_retry_does_not_skip_unprocessed_comments_in_failed_page(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    page_one = [_comment("c1", "user-1", "长沙有没有？"), _comment("c2", "user-1", "120平大概多少钱？")]
    page_two = [_comment("c3", "user-1", "年底准备装。")]
    provider = ScriptedProvider(
        {
            None: CommentScanResult(page_one, "partial", 2, "page-2", True),
            "page-2": CommentScanResult(page_two, "partial", 1, None, False),
        }
    )
    llm = RecordingTextLLM(fail_calls={2, 3})
    service = _service(provider, llm)

    await service.run_task(task_id, full=True)
    with sessions() as db:
        checkpoint = db.get(TaskCheckpoint, task_id)
        failed_run = db.scalar(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.success.is_(False)))
        assert checkpoint.last_comment_cursor == ""
        assert len(checkpoint.processed_comment_ids) == 1
        assert failed_run is not None and failed_run.success is False

    await service.run_task(task_id, full=True)

    with sessions() as db:
        comments_count = len(db.scalars(select(Comment).where(Comment.project_id == project_id)).all())
        report = db.scalar(select(TaskReport).where(TaskReport.task_id == task_id))
        task_status = db.get(ScanTask, task_id).status

    assert task_status == "completed"
    assert comments_count == 3
    assert report is not None and report.metrics["comments"] == 3 and report.metrics["coverage_status"] == "partial"
    assert provider.cursors == [None, None, "page-2"]
    assert llm.calls == 5
    assert all(text in "\n".join(llm.users) for text in ("长沙有没有？", "120平大概多少钱？", "年底准备装。"))


@pytest.mark.asyncio
async def test_pipeline_does_not_claim_complete_when_provider_omits_required_cursor(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = ScriptedProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙多少钱？")], "partial", 1, None, True)})

    await _service(provider, RecordingTextLLM()).run_task(task_id, full=True)

    with sessions() as db:
        task = db.get(ScanTask, task_id)
        comments_count = len(db.scalars(select(Comment).where(Comment.project_id == project_id)).all())
        report = db.scalar(select(TaskReport).where(TaskReport.task_id == task_id))

    assert task.status == "failed"
    assert "未提供游标" in task.error
    assert comments_count == 1
    assert report is not None
    assert report.summary == "行业扫描未完成"
    assert report.metrics["comments_new"] == 1
    assert report.metrics["failure"]


def test_upsert_lead_is_idempotent_and_does_not_merge_anonymous_comments(monkeypatch):
    sessions = _session(monkeypatch)
    db = sessions()
    project = Project(name="测试", industry="装修")
    db.add(project)
    db.flush()
    video = Video(project_id=project.id, platform="douyin", platform_video_id="v1", title="测试视频", keyword="装修")
    db.add(video)
    db.flush()
    first = Comment(project_id=project.id, video_id=video.id, platform="douyin", platform_comment_id="c1", nickname="", content="多少钱？", content_hash="h1")
    second = Comment(project_id=project.id, video_id=video.id, platform="douyin", platform_comment_id="c2", nickname="", content="长沙有吗？", content_hash="h2")
    db.add_all([first, second])
    db.commit()
    judgment = {
        "lead_score": 88,
        "lead_level": "A",
        "intent_level": "high",
        "need": "装修",
        "location": "长沙",
        "budget": "10万",
        "purchase_stage": "咨询",
        "pain_point": "价格",
        "buying_signals": ["询价"],
        "summary": "有需求",
        "reason": "明确询价",
        "recommended_action": "人工跟进",
    }

    _upsert_lead(db, project.id, first, judgment, video.id)
    _upsert_lead(db, project.id, first, judgment, video.id)
    _upsert_lead(db, project.id, second, judgment, video.id)
    db.commit()

    assert len(db.scalars(select(Lead).where(Lead.project_id == project.id)).all()) == 2
    first_lead = db.scalar(select(Lead).join(LeadComment, LeadComment.lead_id == Lead.id).where(LeadComment.comment_id == first.id))
    assert first_lead.occurrence_count == 1
    assert len(db.scalars(select(LeadComment)).all()) == 2
    assert len(db.scalars(select(LeadEvent)).all()) == 2


@pytest.mark.asyncio
async def test_pipeline_auto_reply_policy_creates_review_only_draft(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = ScriptedProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙装修大概多少钱？")], "partial", 1, None, False)})
    llm = RecordingTextLLM()
    with sessions() as db:
        db.add(ReplyPolicy(project_id=project_id, enabled=True, auto_reply_enabled=True, minimum_confidence=0.8, minimum_lead_score=70, allowed_intents=["high"]))
        db.add(KnowledgeEntry(project_id=project_id, title="装修报价", content="长沙装修报价需要结合面积和施工范围评估。", tags=["价格", "报价"], enabled=True))
        db.add(Persona(project_id=project_id, name="顾问", identity="装修顾问", tone="专业克制"))
        db.commit()

    service = _service(provider, llm)
    calls = 0

    async def reply_only_from_text(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ReplyDecision(should_reply=True, confidence=0.9, reply_type="price", reply_text="可以先按面积和施工范围帮你估算。", need_human_review=False, reason="已有报价知识")

    monkeypatch.setattr(service.reply_agent, "run", reply_only_from_text)
    await service.run_task(task_id, full=True)

    with sessions() as db:
        replies = db.scalars(select(CommentReply).where(CommentReply.project_id == project_id)).all()
        runs = db.scalars(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.agent == "ReplyAgent")).all()
        task_error = db.get(ScanTask, task_id).error

    assert calls == 1
    assert len(replies) == 1, task_error
    assert replies[0].status == "WAITING_REVIEW"
    assert replies[0].reply_source == "AI_AUTO"
    assert replies[0].reply_text == "可以先按面积和施工范围帮你估算。"
    assert len(runs) == 1 and runs[0].success is True
    assert provider.cursors == [None]


@pytest.mark.asyncio
async def test_pipeline_explicit_auto_reply_sends_and_persists_verified_result(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = AutoReplyProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙装修大概多少钱？")], "partial", 1, None, False)})
    llm = RecordingTextLLM()
    with sessions() as db:
        db.add(ReplyPolicy(project_id=project_id, enabled=True, auto_reply_enabled=True, minimum_confidence=0.8, minimum_lead_score=70, allowed_intents=["high"]))
        db.add(KnowledgeEntry(project_id=project_id, title="装修报价", content="长沙装修报价需要结合面积和施工范围评估。", tags=["价格", "报价"], enabled=True))
        db.commit()

    service = _service(provider, llm)

    async def safe_reply(*args, **kwargs):
        return ReplyDecision(should_reply=True, confidence=0.9, reply_type="price", reply_text="可以先按面积和施工范围帮你估算。", need_human_review=False, reason="已有报价知识")

    monkeypatch.setattr(service.reply_agent, "run", safe_reply)
    await service.run_task(task_id, full=True)

    with sessions() as db:
        reply = db.scalar(select(CommentReply).where(CommentReply.project_id == project_id))
        task_error = db.get(ScanTask, task_id).error

    assert not task_error
    assert reply is not None
    assert reply.status == "VERIFIED"
    assert reply.approved_at is not None
    assert reply.sent_at is not None
    assert reply.verified_at is not None
    assert provider.reply_calls == [(provider.video.url, "c1", "可以先按面积和施工范围帮你估算。")]


@pytest.mark.asyncio
async def test_pipeline_auto_reply_policy_disabled_does_not_call_reply_agent(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = ScriptedProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙装修大概多少钱？")], "partial", 1, None, False)})
    llm = RecordingTextLLM()
    with sessions() as db:
        db.add(ReplyPolicy(project_id=project_id, enabled=True, auto_reply_enabled=False, minimum_confidence=0.8, minimum_lead_score=70))
        db.commit()

    service = _service(provider, llm)
    calls = 0

    async def unexpected_reply_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("策略关闭时不应调用 ReplyAgent")

    monkeypatch.setattr(service.reply_agent, "run", unexpected_reply_call)
    await service.run_task(task_id, full=True)

    with sessions() as db:
        replies = db.scalars(select(CommentReply).where(CommentReply.project_id == project_id)).all()
        leads = db.scalars(select(Lead).where(Lead.project_id == project_id)).all()

    assert calls == 0
    assert replies == []
    assert len(leads) == 1


@pytest.mark.asyncio
async def test_pipeline_reply_agent_failure_keeps_comment_and_lead(monkeypatch):
    sessions = _session(monkeypatch)
    task_id, project_id = _task(sessions)
    provider = ScriptedProvider({None: CommentScanResult([_comment("c1", "user-1", "长沙装修大概多少钱？")], "partial", 1, None, False)})
    llm = RecordingTextLLM()
    with sessions() as db:
        db.add(ReplyPolicy(project_id=project_id, enabled=True, auto_reply_enabled=True, minimum_confidence=0.8, minimum_lead_score=70, allowed_intents=["high"]))
        db.add(KnowledgeEntry(project_id=project_id, title="装修报价", content="长沙装修报价需要结合面积和施工范围评估。", tags=["价格"], enabled=True))
        db.commit()

    service = _service(provider, llm)

    async def failed_reply_call(*args, **kwargs):
        raise RuntimeError("reply text model unavailable")

    monkeypatch.setattr(service.reply_agent, "run", failed_reply_call)
    await service.run_task(task_id, full=True)

    with sessions() as db:
        comments = db.scalars(select(Comment).where(Comment.project_id == project_id)).all()
        leads = db.scalars(select(Lead).where(Lead.project_id == project_id)).all()
        replies = db.scalars(select(CommentReply).where(CommentReply.project_id == project_id)).all()
        run = db.scalar(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.agent == "ReplyAgent"))
        task_error = db.get(ScanTask, task_id).error

    assert len(comments) == 1
    assert len(leads) == 1, task_error
    assert replies == []
    assert run is not None and run.success is False and "reply text model unavailable" in run.error
