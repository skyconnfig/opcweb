import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.keyword_agent import keyword_opportunity_score
from app.agents.industry_agent import IndustryAgent
from app.agents.keyword_agent import KeywordAgent
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.llm import BaseLLMProvider, LLMCall, OpenAICompatibleProvider, is_text_only_model
from app.agents.reply_agent import ReplyAgent
from app.agents.persona_agent import PersonaAgent
from app.agents.lead_assistant_agent import LeadAssistantAgent
from app.errors import LLMNotConfiguredError
from app.agents.radar_agent import RadarAgent
from app.core.config import Settings
from app.db import Base
from app.main import ReplyActionIn, ReplyBatchIn, ReplyPolicyIn, ScheduleIn, _require_provider_capability, send_comment_reply
from app.models import AgentRun, BrowserProfile, BrowserSession, Comment, CommentReply, DouyinAccount, KnowledgeEntry, Lead, Project, ReplyPolicy, ScanSchedule, ScanTask, TaskCheckpoint, Video, now_utc
from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.external.social_harvest import SocialHarvestExternalProvider
from app.providers.douyin.dto import ReplyResult, ReplyStatus
from app.providers.douyin.dto import LoginStatus
from app.main import SettingsInput
from app.services.radar_service import fingerprint, lead_level
from app.services.event_bus import sse_line
from app.services.event_bus import event_bus
from app.services.reply_policy import enforce_send_policy, record_reply_verification, recover_stale_sending


def test_api_provider_capability_guard_rejects_explicitly_unsupported_action():
    class Provider:
        name = "Text-only adapter"
        capabilities = {"keyword_search": True, "comments": False}

    with pytest.raises(HTTPException) as caught:
        _require_provider_capability(Provider(), "comments", "公开评论采集")

    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "PROVIDER_CAPABILITY_UNSUPPORTED"
    assert caught.value.detail["detail"] == {"provider": "Text-only adapter", "capability": "comments"}
from app.settings_store import decrypt_secret, encrypt_secret
from app.tasks.checkpoint import checkpoint_snapshot
from app.tasks.queue import advance_schedule, claim_next_task, enqueue_scan, has_active_scan
from app.tasks.scheduler import enqueue_due_schedules
from app.security import is_authorized
from starlette.requests import Request


def test_keyword_score_weights_and_levels():
    assert keyword_opportunity_score(100, 100, 100, 100, 100, 100) == 100
    assert lead_level(95) == "S"
    assert lead_level(80) == "A"
    assert lead_level(65) == "B"


def test_rule_prefilter_removes_noise():
    rule = RulePreFilter()
    assert rule.should_analyze("长沙有没有靠谱的装修公司")
    assert not rule.should_analyze("666")
    assert not rule.should_analyze("🙂🙂")


def test_rule_prefilter_deduplicates_comment_text():
    rule = RulePreFilter()
    seen = set()
    assert rule.should_analyze("长沙有没有靠谱的装修公司", seen)
    assert not rule.should_analyze("长沙 有没有靠谱的装修公司", seen)


def test_fingerprint_is_whitespace_stable():
    assert fingerprint("长沙 装修") == fingerprint("长沙装修")


def test_sse_and_checkpoint_contract():
    assert 'id: 8\n' in sse_line({"id": 8, "event_type": "lead.detected", "message": "发现潜客"})
    assert '"event_type": "lead.detected"' in sse_line({"id": 8, "event_type": "lead.detected", "message": "发现潜客"})
    class Checkpoint:
        last_keyword_id = 3
        last_video_id = 8
        last_comment_cursor = "cursor-2"
        processed_comment_ids = [1, 2]
    assert checkpoint_snapshot(Checkpoint()) == {"last_keyword_id": 3, "last_video_id": 8, "last_comment_cursor": "cursor-2", "processed_comment_ids": [1, 2]}


async def test_event_bus_preserves_project_scope():
    queue = event_bus.subscribe()
    try:
        await event_bus.publish({"id": 8, "project_id": 3, "event_type": "video.discovered", "message": "发现视频"})
        assert (await queue.get())["project_id"] == 3
    finally:
        event_bus.unsubscribe(queue)


@pytest.mark.parametrize(
    ("configured_interval", "expected_interval"),
    [(10, 10), (15, 15), (30, 30), (5, 10), (45, 30)],
)
def test_scan_schedule_advances_within_the_ten_to_thirty_minute_window(configured_interval, expected_interval):
    class Schedule:
        interval_minutes = configured_interval
        last_run_at = None
        next_run_at = None

    now = datetime.now(timezone.utc)
    schedule = Schedule()
    advance_schedule(schedule, now)
    assert schedule.last_run_at == now
    assert schedule.next_run_at == now + timedelta(minutes=expected_interval)


def test_schedule_request_rejects_intervals_outside_the_ten_to_thirty_minute_window():
    with pytest.raises(ValueError):
        ScheduleIn(interval_minutes=9)
    with pytest.raises(ValueError):
        ScheduleIn(interval_minutes=31)


def test_schedule_update_applies_a_changed_interval_on_the_next_run():
    from app import main

    db = _reply_test_session()
    project = Project(name="采集计划项目", industry="装修")
    db.add(project)
    db.commit()
    old_next_run_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
    db.add(ScanSchedule(project_id=project.id, enabled=True, interval_minutes=30, next_run_at=old_next_run_at))
    db.commit()

    saved = main.put_project_schedule(project.id, ScheduleIn(enabled=True, interval_minutes=10), db)
    next_run_at = datetime.fromisoformat(saved["next_run_at"]).replace(tzinfo=None)

    assert saved["interval_minutes"] == 10
    assert next_run_at < old_next_run_at
    assert timedelta(minutes=9) < next_run_at - datetime.now(timezone.utc).replace(tzinfo=None) < timedelta(minutes=11)


@pytest.mark.asyncio
async def test_due_schedule_enqueues_a_scan_and_advances_next_run(monkeypatch):
    db = _reply_test_session()
    project = Project(name="到点采集项目", industry="装修")
    db.add(project)
    db.commit()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    schedule = ScanSchedule(project_id=project.id, enabled=True, interval_minutes=10, next_run_at=now - timedelta(seconds=1))
    db.add(schedule)
    db.commit()

    @contextmanager
    def session_context():
        yield db

    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", session_context)

    assert await enqueue_due_schedules() == 1
    task = db.scalar(select(ScanTask).where(ScanTask.project_id == project.id))
    assert task is not None
    assert task.status == "queued"
    assert schedule.last_run_at is not None
    next_run_at = schedule.next_run_at.replace(tzinfo=timezone.utc) if schedule.next_run_at.tzinfo is None else schedule.next_run_at
    assert timedelta(minutes=9) < next_run_at - now.replace(tzinfo=timezone.utc) < timedelta(minutes=11)


@pytest.mark.asyncio
async def test_enabled_schedule_without_next_run_is_repaired_and_executed(monkeypatch):
    db = _reply_test_session()
    project = Project(name="旧计划项目", industry="装修")
    db.add(project)
    db.commit()
    schedule = ScanSchedule(project_id=project.id, enabled=True, interval_minutes=30, next_run_at=None)
    db.add(schedule)
    db.commit()

    @contextmanager
    def session_context():
        yield db

    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", session_context)

    assert await enqueue_due_schedules() == 1
    assert db.scalar(select(ScanTask).where(ScanTask.project_id == project.id)) is not None
    assert schedule.next_run_at is not None


@pytest.mark.asyncio
async def test_due_schedule_does_not_enqueue_while_project_has_active_scan(monkeypatch):
    db = _reply_test_session()
    project = Project(name="防重复采集项目", industry="装修")
    db.add(project)
    db.commit()
    enqueue_scan(db, project.id)
    schedule = ScanSchedule(project_id=project.id, enabled=True, interval_minutes=15, next_run_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1))
    db.add(schedule)
    db.commit()

    @contextmanager
    def session_context():
        yield db

    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", session_context)

    assert await enqueue_due_schedules() == 0
    assert db.query(ScanTask).filter(ScanTask.project_id == project.id).count() == 1
    assert schedule.next_run_at is not None


def test_verification_required_scan_blocks_duplicate_project_work():
    db = _reply_test_session()
    project = Project(name="验证中项目", industry="装修")
    db.add(project)
    db.commit()
    waiting = enqueue_scan(db, project.id)
    waiting.status = "verification_required"
    db.commit()

    assert has_active_scan(db, project.id) is True
    queued = enqueue_scan(db, project.id)
    assert queued.status == "queued"
    assert claim_next_task(db) is None


@pytest.mark.asyncio
async def test_due_schedule_waits_for_real_provider_health(monkeypatch):
    db = _reply_test_session()
    project = Project(name="等待真实数据源项目", industry="装修", status="ready")
    db.add(project)
    db.commit()
    due_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    schedule = ScanSchedule(project_id=project.id, enabled=True, interval_minutes=10, next_run_at=due_at)
    db.add(schedule)
    db.commit()

    @contextmanager
    def session_context():
        yield db

    class UnhealthyProvider:
        async def health_check(self):
            return type("Health", (), {"status": "login_required"})()

    monkeypatch.setattr("app.tasks.scheduler.SessionLocal", session_context)
    assert await enqueue_due_schedules(provider=UnhealthyProvider()) == 0
    assert db.query(ScanTask).filter(ScanTask.project_id == project.id).count() == 0
    assert schedule.next_run_at.replace(tzinfo=None) == due_at


def test_task_claim_is_atomic_and_serializes_same_project():
    db = _reply_test_session()
    project = Project(name="任务项目", industry="装修")
    db.add(project)
    db.commit()
    first = enqueue_scan(db, project.id)
    second = enqueue_scan(db, project.id, full=True)

    assert claim_next_task(db) == (first.id, False)
    assert claim_next_task(db) is None

    db.get(type(first), first.id).status = "completed"
    db.commit()
    assert claim_next_task(db) == (second.id, True)


def test_task_claim_rechecks_verification_state_before_commit(monkeypatch):
    """A verification task created after candidate selection must win the race."""
    from sqlalchemy.sql.dml import Update

    db = _reply_test_session()
    project = Project(name="验证竞争项目", industry="装修")
    db.add(project)
    db.commit()
    candidate = enqueue_scan(db, project.id)
    original_execute = db.execute
    injected = False

    def execute(statement, *args, **kwargs):
        nonlocal injected
        if isinstance(statement, Update) and not injected:
            db.add(ScanTask(project_id=project.id, name="人工验证中的任务", status="verification_required"))
            db.flush()
            injected = True
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute)
    assert claim_next_task(db) is None
    assert db.get(ScanTask, candidate.id).status == "queued"


@pytest.mark.asyncio
async def test_task_controls_enforce_transitions_and_retry_from_checkpoint():
    from app import main

    db = _reply_test_session()
    project = Project(name="任务控制项目", industry="装修")
    db.add(project)
    db.commit()
    task = enqueue_scan(db, project.id)

    paused = main.pause_task(task.id, db)
    assert paused.status == "paused"
    resumed = await main.resume_task(task.id, db)
    assert resumed.status == "queued"
    task.status = "completed"
    db.commit()
    with pytest.raises(HTTPException) as invalid_pause:
        main.pause_task(task.id, db)
    assert invalid_pause.value.status_code == 409

    task = db.get(ScanTask, task.id)
    task.status = "failed"
    task.error = "暂时失败"
    checkpoint = db.get(TaskCheckpoint, task.id)
    checkpoint.last_keyword_id = 7
    checkpoint.last_video_id = 11
    checkpoint.last_comment_cursor = "cursor-2"
    checkpoint.processed_comment_ids = [1, 2, 3]
    db.commit()

    retried = await main.retry_task(task.id, db)
    assert retried.status == "queued"
    assert retried.error == ""
    assert db.get(TaskCheckpoint, task.id).processed_comment_ids == [1, 2, 3]
    assert db.get(TaskCheckpoint, task.id).last_comment_cursor == "cursor-2"


@pytest.mark.asyncio
async def test_verification_task_is_requeued_after_login_state_recovers(monkeypatch):
    from app import main
    from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider

    db = _reply_test_session()
    engine = db.get_bind()
    project = Project(name="验证恢复项目", industry="装修")
    db.add(project)
    db.commit()
    task = enqueue_scan(db, project.id)
    db.add(BrowserSession(project_id=project.id, profile_path="profile-verification", status="VERIFICATION_REQUIRED", last_error="需要人工验证"))
    task.status = "verification_required"
    task.error = "抖音页面需要人工完成安全验证"
    task.finished_at = None
    db.commit()
    db.close()
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    published = []

    class RecoveredProvider(DouyinPlaywrightProvider):
        async def get_login_status(self):
            return LoginStatus.LOGGED_IN

    provider = RecoveredProvider(browser_manager=object())

    async def publish(event):
        published.append(event)

    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "active_provider_for_project", lambda _db, _project_id: provider)
    monkeypatch.setattr(main.event_bus, "publish", publish)

    await main._resume_verification_tasks()

    check = sessions()
    resumed = check.get(ScanTask, task.id)
    assert resumed.status == "queued"
    assert resumed.error == ""
    assert resumed.finished_at is None
    session = check.scalar(select(BrowserSession).where(BrowserSession.project_id == project.id))
    assert session.status == "READY"
    assert session.last_error == ""
    assert session.last_check_time is not None
    assert published[-1]["event_type"] == "task.verification_resolved"
    assert published[-1]["payload"]["resume"] == "checkpoint"
    check.close()


def test_douyin_browser_state_is_persisted_without_credentials():
    from app import main

    class Browser:
        profile_dir = "data/browser/test-account"
        channel = "chromium"
        headless = False
        is_running = True

    class Provider:
        browser = Browser()

    db = _reply_test_session()
    account = main._sync_douyin_account(db, Provider(), LoginStatus.LOGGED_IN)
    stored = db.get(DouyinAccount, account.id)
    profile = db.scalar(select(BrowserProfile).where(BrowserProfile.account_id == account.id))
    assert stored and stored.status == "LOGGED_IN" and stored.last_login_at is not None
    assert profile and profile.status == "ACTIVE" and profile.profile_dir == "data/browser/test-account"

    Provider.browser.is_running = False
    main._sync_douyin_account(db, Provider(), None)
    assert db.get(DouyinAccount, account.id).status == "BROWSER_STOPPED"
    assert db.scalar(select(BrowserProfile).where(BrowserProfile.account_id == account.id)).status == "INACTIVE"


def test_douyin_account_identity_is_persisted_when_dom_provides_it():
    from app import main

    class Browser:
        profile_dir = "data/browser/identity-account"
        channel = "chromium"
        headless = False
        is_running = True

    class Provider:
        browser = Browser()

    db = _reply_test_session()
    account = main._sync_douyin_account(
        db,
        Provider(),
        LoginStatus.LOGGED_IN,
        {"nickname": "真实昵称", "douyin_user_id": "real-user-9"},
    )

    assert account.nickname == "真实昵称"
    assert account.douyin_user_id == "real-user-9"


async def test_text_only_agent_chain_and_history_context():
    project = {"industry": "装修", "location": "长沙", "service": "旧房翻新", "target_customer": "准备装修的长沙业主", "price_range": "5万-30万", "description": "提供设计与施工"}
    with pytest.raises(LLMNotConfiguredError):
        await IndustryAgent().run(project)
    with pytest.raises(LLMNotConfiguredError):
        await LeadJudgeAgent().run(project, {"content": "年底准备装"})


@pytest.mark.asyncio
async def test_lead_judge_does_not_retry_non_contract_errors():
    class FailingProvider(BaseLLMProvider):
        configured = True
        model = "test-text-model"

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def structured_output(self, system, user, schema):
            self.calls += 1
            raise RuntimeError("text provider unavailable")

    provider = FailingProvider()
    with pytest.raises(RuntimeError, match="text provider unavailable"):
        await LeadJudgeAgent(provider).run({"industry": "装修"}, {"content": "120平多少钱"})
    assert provider.calls == 1


def test_lead_judge_accepts_required_text_contract_without_extra_intent_field():
    result = LeadJudgeAgent()._normalize(
        {
            "is_lead": True,
            "confidence": 0.9,
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
        },
        {},
        {"content": "年底准备装"},
    )
    assert result["lead_level"] == "A"
    assert result["intent"] == "high"


def test_lead_judge_normalizes_localized_level_before_deriving_from_score():
    payload = {
        "is_lead": False,
        "confidence": 0.2,
        "lead_score": 10,
        "lead_level": "低",
        "intent_level": "低",
        "need": "",
        "location": None,
        "budget": None,
        "time_requirement": None,
        "purchase_stage": "unknown",
        "pain_point": "",
        "buying_signals": [],
        "summary": "普通互动",
        "reason": "未发现购买信号",
        "recommended_action": "observe",
        "should_reply": False,
    }

    result = LeadJudgeAgent()._normalize(payload, {}, {"content": "感谢分享"})

    assert result["lead_level"] == "C"


def test_lead_judge_normalizes_numeric_intent_and_signal_text():
    payload = {
        "is_lead": 0,
        "confidence": 0.2,
        "lead_score": 10,
        "lead_level": "低",
        "intent_level": 0,
        "need": None,
        "location": None,
        "budget": None,
        "time_requirement": None,
        "purchase_stage": 0,
        "pain_point": None,
        "buying_signals": "无明确需求, 普通互动",
        "summary": None,
        "reason": None,
        "recommended_action": None,
        "should_reply": 0,
    }

    result = LeadJudgeAgent()._normalize(payload, {}, {"content": "感谢分享"})

    assert result["intent_level"] == "low"
    assert result["buying_signals"] == ["无明确需求", "普通互动"]


def test_persona_agent_normalizes_common_text_model_field_aliases():
    result = PersonaAgent._normalize(
        {
            "insight": "用户正在比较方案",
            "strategy": "先回答范围，再确认户型",
            "reply": "可以先说一下户型和预算，我按长沙本地情况帮你拆解。",
            "question": "方便说下大概面积吗？",
            "risk_warnings": "不要承诺最低价；不要索取联系方式",
        },
        {},
        {},
    )

    assert result["customer_insight"] == "用户正在比较方案"
    assert result["recommended_reply"].startswith("可以先说")
    assert result["warnings"] == ["不要承诺最低价", "不要索取联系方式"]


def test_lead_assistant_uses_text_context_and_returns_review_fields():
    class TextAssistant(BaseLLMProvider):
        configured = True
        model = "text-model"

        async def structured_output(self, system, user, schema):
            self.user_text = user
            self.last_call = LLMCall(self.model, user, {"reply": "先确认面积和预算。"}, 12, 3, True, "")
            return {
                "reply": "先确认面积和预算。",
                "reason": "客户已经表达了明确的购买问题。",
                "risk": "报价需以现场需求为准。",
                "next_action": "人工确认后继续询问户型和时间。",
            }

    provider = TextAssistant()
    decision = asyncio.run(
        LeadAssistantAgent(provider).run(
            {"industry": "装修", "video_url": "https://example.com/video"},
            {"need": "120平装修", "lead_score": 88},
            [{"content": "120平大概多少钱？"}],
            [{"title": "报价规则", "content": "需要结合面积和方案评估"}],
            {"tone": "专业克制"},
        )
    )

    assert decision.reply.startswith("先确认")
    assert set(decision.model_dump()) == {"reply", "reason", "risk", "next_action"}
    assert "video_url" not in provider.user_text
    assert "120平大概多少钱" in provider.user_text


@pytest.mark.asyncio
async def test_lead_assistant_endpoint_persists_advice_and_audit_run(monkeypatch):
    from app import main
    from app.models import KnowledgeEntry, LeadComment

    class TextAssistant(BaseLLMProvider):
        configured = True
        model = "text-model"

        async def structured_output(self, system, user, schema):
            self.last_call = LLMCall(self.model, f"{system}\n{user}", {}, 9, 4, True, "")
            return {"reply": "我先帮你确认需求。", "reason": "评论表达了明确的咨询意图。", "risk": "具体方案需要人工核实。", "next_action": "人工确认面积和时间。"}

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    lead = Lead(project_id=comment.project_id, platform="douyin", platform_user_id="assistant-user", nickname="咨询客户", lead_score=88, lead_level="A", need="装修报价")
    db.add(lead)
    db.flush()
    db.add(LeadComment(lead_id=lead.id, comment_id=comment.id))
    db.add(KnowledgeEntry(project_id=comment.project_id, title="服务范围", content="先确认面积和时间，再提供方案", enabled=True))
    db.commit()
    monkeypatch.setattr(main, "active_llm", lambda _db: TextAssistant())

    result = await main.lead_assistant(lead.id, db)

    assert set(result) == {"reply", "reason", "risk", "next_action"}
    assert result["reply"].startswith("我先")
    db.refresh(lead)
    assert lead.persona_advice["next_action"] == "人工确认面积和时间。"
    run = db.scalar(select(AgentRun).where(AgentRun.agent == "LeadAssistantAgent"))
    assert run is not None and run.success is True and run.prompt_version == "lead_assistant_text_v1"


def test_lead_judge_rejects_string_true_that_is_not_a_boolean():
    payload = {
        "is_lead": "false",
        "confidence": 0.2,
        "lead_score": 10,
        "intent_level": "low",
        "need": "",
        "location": None,
        "budget": None,
        "time_requirement": None,
        "purchase_stage": "unknown",
        "pain_point": "",
        "buying_signals": [],
        "summary": "无明确需求",
        "reason": "测试",
        "recommended_action": "observe",
        "should_reply": "false",
    }
    assert LeadJudgeAgent()._normalize(payload, {}, {"content": "路过"})["is_lead"] is False


def test_lead_upsert_persists_time_and_confidence():
    from app.services.radar_service import _upsert_lead

    db = _reply_test_session()
    project = Project(name="时间字段项目", industry="装修")
    db.add(project)
    db.flush()
    video = Video(project_id=project.id, platform_video_id="time-video", title="真实视频", keyword="装修")
    db.add(video)
    db.flush()
    comment = Comment(project_id=project.id, video_id=video.id, platform_comment_id="time-comment", platform_user_id="time-user", content="年底准备装", content_hash="time-hash")
    db.add(comment)
    db.flush()
    _upsert_lead(db, project.id, comment, {"confidence": 0.93, "lead_score": 88, "lead_level": "A", "intent_level": "high", "need": "装修", "location": "长沙", "budget": "10万", "time_requirement": "年底", "purchase_stage": "准备咨询", "pain_point": "担心增项", "buying_signals": ["明确时间"], "summary": "有计划", "reason": "时间明确", "recommended_action": "人工跟进"}, video.id)
    db.commit()
    lead = db.scalar(select(Lead).where(Lead.project_id == project.id))
    assert lead and lead.time_requirement == "年底" and lead.confidence == 0.93


class _ReplyTextProvider(BaseLLMProvider):
    configured = True
    model = "test-text-model"

    def __init__(self):
        super().__init__()
        self.user_text = ""

    async def structured_output(self, system, user, schema):
        self.user_text = user
        self.last_call = LLMCall(self.model, user, {}, tokens=12, latency_ms=4)
        return {
            "should_reply": True,
            "confidence": 0.91,
            "reply_type": "price",
            "reply_text": "可以先按面积和具体需求估算，方便的话说一下大概面积？",
            "need_human_review": True,
            "reason": "先确认面积再给出可核验的估算",
            "risk_flags": [],
        }


async def test_reply_agent_requires_matching_text_knowledge_and_filters_media():
    provider = _ReplyTextProvider()
    decision = await ReplyAgent(provider).run(
        {"industry": "装修", "service": "旧房翻新", "location": "长沙"},
        {"content": "120平大概多少钱？", "video_url": "https://www.douyin.com/video/real"},
        knowledge=[{"title": "报价规则", "content": "旧房翻新需要先确认面积和现场情况", "tags": ["价格"], "enabled": True}],
    )
    assert decision.should_reply is True
    assert decision.need_human_review is True
    assert "video_url" not in provider.user_text


async def test_reply_agent_blocks_without_knowledge_without_calling_llm():
    provider = _ReplyTextProvider()
    decision = await ReplyAgent(provider).run(
        {"industry": "教育", "service": "课程"},
        {"content": "你们怎么收费？"},
        knowledge=[],
    )
    assert decision.should_reply is False
    assert decision.need_human_review is True
    assert "KNOWLEDGE_INSUFFICIENT" in decision.risk_flags
    assert provider.user_text == ""


async def test_reply_agent_retries_once_after_invalid_structured_output():
    class RetryProvider(_ReplyTextProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def structured_output(self, system, user, schema):
            self.calls += 1
            if self.calls == 1:
                self.last_call = LLMCall(self.model, user, {}, tokens=4, latency_ms=1)
                return {"should_reply": "not-a-bool"}
            return await super().structured_output(system, user, schema)

    provider = RetryProvider()
    decision = await ReplyAgent(provider).run(
        {"industry": "装修", "service": "旧房翻新", "location": "长沙"},
        {"content": "120平大概多少钱？"},
        knowledge=[{"title": "报价规则", "content": "旧房翻新需要先确认面积和现场情况", "tags": ["价格"], "enabled": True}],
    )
    assert decision.should_reply is True
    assert provider.calls == 2


def test_radar_uses_text_and_metadata_fields():
    result = RadarAgent().score({"title": "长沙120平装修，这5个增项一定注意", "description": "长沙本地装修经验分享", "creator": "装修老李", "publish_time": "2026-09-01T00:00:00+00:00", "likes": 1200, "comments": 328, "shares": 80, "collects": 100, "cover": "https://example.com/cover.jpg"}, "长沙装修避坑")
    assert {"industry_relevance_score", "commercial_relevance_score", "lead_opportunity_score", "video_opportunity_score", "level"} <= result.keys()
    assert 0 <= result["video_opportunity_score"] <= 100


def test_prefilter_drops_at_least_half_obvious_noise():
    samples = ["666", "哈哈哈", "支持", "来了", "主播真帅", "🙂🙂", "收藏了", "讲得很好", "长沙有没有靠谱的装修公司", "我家120平多少钱"]
    kept = sum(RulePreFilter().should_analyze(item) for item in samples)
    assert kept <= len(samples) / 2


async def test_openai_compatible_provider_sends_text_only_json():
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 7}})

    provider = OpenAICompatibleProvider(Settings(llm_base_url="https://text.example/v1", llm_api_key="test", llm_model="text-model"), transport=httpx.MockTransport(handler))
    assert await provider.structured_output("system text", "user text", {"type": "object"}) == {"ok": True}
    assert captured["body"]["model"] == "text-model"
    assert all(isinstance(message["content"], str) for message in captured["body"]["messages"])
    assert provider.last_call and provider.last_call.tokens == 7 and provider.last_call.success is True


@pytest.mark.asyncio
async def test_deepseek_v4_uses_text_content_blocks_and_disables_reasoning_for_json():
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 3}})

    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://api.deepseek.com", llm_api_key="test", llm_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    assert await provider.structured_output("system text", "user text", {"type": "object"}) == {"ok": True}
    assert all(isinstance(message["content"], list) for message in captured["body"]["messages"])
    assert captured["body"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_deepseek_v4_retries_opposite_text_content_shape_on_schema_mismatch():
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "messages[0]: invalid type: sequence, expected a string"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 3}})

    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://api.deepseek.com", llm_api_key="test", llm_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    assert await provider.structured_output("system text", "user text", {"type": "object"}) == {"ok": True}
    assert isinstance(bodies[0]["messages"][0]["content"], list)
    assert isinstance(bodies[1]["messages"][0]["content"], str)
    assert provider.last_call and provider.last_call.success is True


@pytest.mark.asyncio
async def test_llm_connection_uses_text_completion_and_parses_json():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "text-model"
        assert body["response_format"] == {"type": "json_object"}
        assert all(isinstance(message["content"], str) for message in body["messages"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 5}},
        )

    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://text.example/v1", llm_api_key="test", llm_model="text-model"),
        transport=httpx.MockTransport(handler),
    )

    result = await provider.test_connection()

    assert result == {"ok": True, "message": "连接成功：text-model"}
    assert len(requests) == 1
    assert provider.last_call and provider.last_call.success is True
    assert provider.last_call.output_json == {"ok": True}


@pytest.mark.asyncio
async def test_llm_connection_rejects_unparseable_text_response():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://text.example/v1", llm_api_key="test", llm_model="text-model"),
        transport=httpx.MockTransport(handler),
    )

    result = await provider.test_connection()

    assert result["ok"] is False
    assert result["code"] == "LLM_INVALID_RESPONSE"
    assert provider.last_call and provider.last_call.success is False


@pytest.mark.asyncio
async def test_llm_connection_does_not_request_when_unconfigured():
    def handler(request: httpx.Request):
        raise AssertionError("an unconfigured provider must not make a request")

    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://text.example/v1", llm_api_key="", llm_model="text-model"),
        transport=httpx.MockTransport(handler),
    )

    assert await provider.test_connection() == {
        "ok": False,
        "code": "LLM_NOT_CONFIGURED",
        "message": "请先填写 Base URL、API Key 和 Model",
    }


@pytest.mark.asyncio
async def test_llm_rejects_vision_and_multimodal_model_names_without_requesting():
    def handler(request: httpx.Request):
        raise AssertionError("a non-text model must never be requested")

    assert is_text_only_model("deepseek-v4-flash")
    assert not is_text_only_model("DeepSeek-V4-Flash-Vision-Exp")
    assert not is_text_only_model("qwen-vl-max")
    provider = OpenAICompatibleProvider(
        Settings(llm_base_url="https://text.example/v1", llm_api_key="test", llm_model="qwen-vl-max"),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.test_connection()
    assert result["ok"] is False
    assert result["code"] == "LLM_TEXT_ONLY_MODEL_REQUIRED"


@pytest.mark.asyncio
async def test_project_scoped_list_endpoints_require_project_id_over_http():
    from app import main

    paths = [
        "/api/videos",
        "/api/comments",
        "/api/replies",
        "/api/leads",
        "/api/follow-tasks",
        "/api/follow-task-reminders",
        "/api/tasks",
        "/api/dashboard",
        "/api/analytics",
        "/api/agent-runs",
        "/api/events/stream",
    ]
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        responses = [await client.get(path) for path in paths]

    assert all(response.status_code == 422 for response in responses), [
        (path, response.status_code, response.text) for path, response in zip(paths, responses) if response.status_code != 422
    ]



def test_llm_api_key_is_encrypted_at_rest():
    key = Fernet.generate_key().decode()
    settings = Settings(settings_encryption_key=key)
    stored = encrypt_secret("sk-secret", settings)
    assert stored.startswith("enc:v1:")
    assert "sk-secret" not in stored
    assert decrypt_secret(stored, settings) == "sk-secret"


def test_settings_input_accepts_frontend_readback_values():
    payload = SettingsInput.model_validate(
        {
            "llm_base_url": "https://api.example.com/v1",
            "llm_api_key": "",
            "llm_model": "text-model",
            "llm_temperature": 0.2,
            "llm_timeout": 45.0,
            "llm_api_key_configured": False,
        }
    )
    assert payload.llm_temperature == 0.2
    assert payload.llm_timeout == 45.0


@pytest.mark.asyncio
async def test_test_llm_endpoint_rejects_non_text_model_before_request():
    from app import main

    db = _reply_test_session()
    with pytest.raises(HTTPException) as blocked:
        await main.test_llm(main.SettingsInput(llm_model="DeepSeek-V4-Flash-Vision-Exp"), db)
    assert blocked.value.status_code == 422
    assert "文本模型" in str(blocked.value.detail)


def test_readiness_probe_is_public_when_api_auth_is_enabled(monkeypatch):
    monkeypatch.setattr("app.security.get_settings", lambda: Settings(api_auth_token="secret"))
    request = Request({"type": "http", "method": "GET", "path": "/ready", "headers": [], "query_string": b""})
    assert is_authorized(request)


async def test_social_harvest_report_normalizes_comment_pages(tmp_path):
    report = tmp_path / "task-report.json"
    report.write_text(json.dumps({"coverage_status": "complete", "videos": [], "comments": {"video-1": [{"comment_id": "c1", "user_id": "u1", "nickname": "客户", "content": "长沙多少钱？"}, {"comment_id": "c2", "user_id": "u1", "nickname": "客户", "content": "年底准备"}]}}, ensure_ascii=False), encoding="utf-8")
    provider = SocialHarvestExternalProvider(str(report))
    page = await provider.get_comments("video-1", "0")
    assert page.coverage_status == "complete"
    assert page.items_received == 2
    assert page.items[0].content == "长沙多少钱？"


async def test_douyin_crawler_keyword_comment_response_is_consumable():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.url.path.endswith("/api/collect/search"):
            return httpx.Response(200, json={"success": True, "task_id": "task-1"})
        if request.url.path.endswith("/api/collect/status/task-1"):
            return httpx.Response(200, json={"status": "completed", "collected_count": 1, "data": [{"video_id": "v1", "video_url": "https://www.douyin.com/video/v1", "title": "长沙装修预算", "author": "装修老李", "likes": "1.2万"}]})
        return httpx.Response(200, json={"comments": [{"comment_id": "c1", "comment": "长沙多少钱？"}, {"comment_id": "c2", "comment": "年底准备装"}]})

    provider = DouyinCommentsCrawlerExternalProvider("https://crawler.example", transport=httpx.MockTransport(handler))
    videos = await provider.search_videos("长沙装修", 5)
    page = await provider.get_comments(videos[0].video_id)
    assert len(videos) == 1
    assert videos[0].video_id == "v1"
    assert videos[0].url == "https://www.douyin.com/video/v1"
    assert page.items_received == 2
    assert page.items[0].content == "长沙多少钱？"
    assert all(item.id_source == "platform_field" for item in page.items)
    assert [request.url.path for request in calls] == [
        "/api/collect/search",
        "/api/collect/status/task-1",
        "/api/video/comments",
    ]


async def test_douyin_crawler_uses_returned_video_url_for_comment_scan():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.url.path.endswith("/api/collect/search"):
            return httpx.Response(200, json={"task_id": "task-2"})
        if request.url.path.endswith("/api/collect/status/task-2"):
            return httpx.Response(200, json={"status": "completed", "data": [{"video_id": "v1", "video_url": "https://www.douyin.com/video/v1", "title": "长沙装修预算"}]})
        return httpx.Response(200, json={"comments": [{"comment_id": "c1", "content": "长沙多少钱？"}]})

    provider = DouyinCommentsCrawlerExternalProvider("https://crawler.example", transport=httpx.MockTransport(handler))
    videos = await provider.search_videos("长沙装修", 1)
    await provider.get_comments(videos[0].video_id)
    assert len(calls) == 3
    assert json.loads(calls[2].content)["video_url"] == "https://www.douyin.com/video/v1"


@pytest.mark.asyncio
async def test_douyin_crawler_skips_comments_without_platform_id_and_caches_each_cursor():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        payload = json.loads(request.content)
        if payload.get("cursor") == "page-2":
            return httpx.Response(200, json={"comments": [{"comment_id": "c2", "content": "第二页"}], "next_cursor": None, "has_more": False})
        return httpx.Response(200, json={"comments": [{"content": "没有稳定 ID"}, {"comment_id": "c1", "content": "第一页"}], "next_cursor": "page-2", "has_more": True})

    provider = DouyinCommentsCrawlerExternalProvider("https://crawler.example", transport=httpx.MockTransport(handler))
    first = await provider.get_comments("v1")
    first_again = await provider.get_comments("v1")
    second = await provider.get_comments("v1", "page-2")
    second_again = await provider.get_comments("v1", "page-2")

    assert [item.comment_id for item in first.items] == ["c1"]
    assert first.next_cursor == "page-2" and first.has_more is True
    assert [item.comment_id for item in second.items] == ["c2"]
    assert first_again is first
    assert second_again is second
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_douyin_playwright_dom_comments_have_opaque_cursor_pages(monkeypatch):
    from contextlib import asynccontextmanager

    from app.providers.douyin.dto import DouyinCommentDTO
    from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider

    class Items:
        async def count(self):
            return 3

        def nth(self, index):
            return index

    class Browser:
        @asynccontextmanager
        async def locked_page(self):
            yield object()

    provider = DouyinPlaywrightProvider(browser_manager=Browser())
    provider.comment_page_size = 2
    items = Items()
    comments = [
        DouyinCommentDTO("douyin", f"c{index}", "u", "客户", "", f"评论 {index}")
        for index in range(3)
    ]

    async def no_op(*args, **kwargs):
        return None

    async def find(*args, **kwargs):
        return object()

    async def find_all(*args, **kwargs):
        return items

    async def parse(item, video_url, *, page):
        return comments[item]

    monkeypatch.setattr(provider, "_require_login", no_op)
    monkeypatch.setattr(provider, "_navigate_if_needed", no_op)
    monkeypatch.setattr(provider, "_find", find)
    monkeypatch.setattr(provider, "_find_all", find_all)
    monkeypatch.setattr(provider, "_scroll_comments", no_op)
    monkeypatch.setattr(provider, "_load_more_comments", no_op)
    monkeypatch.setattr(provider, "_expand_reply_threads", no_op)
    monkeypatch.setattr(provider, "_parse_comment", parse)

    first = await provider.get_comments("video-1")
    second = await provider.get_comments("video-1", first.next_cursor)

    assert [item.comment_id for item in first.items] == ["c0", "c1"]
    assert first.coverage_status == "partial"
    assert first.next_cursor == "dom:2" and first.has_more is True
    assert [item.comment_id for item in second.items] == ["c2"]
    assert second.next_cursor is None and second.has_more is False

    with pytest.raises(Exception, match="超出当前 DOM 范围"):
        await provider.get_comments("video-1", "dom:9")


def test_douyin_playwright_rejects_invalid_dom_cursor():
    from app.providers.douyin.exceptions import DouyinPageParseError
    from app.providers.douyin.playwright_provider import _parse_dom_cursor

    with pytest.raises(DouyinPageParseError):
        _parse_dom_cursor("not-a-cursor")


@pytest.mark.asyncio
async def test_douyin_playwright_surfaces_comment_control_click_failure():
    from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider
    from app.providers.douyin.exceptions import DouyinPageParseError

    class Locator:
        async def count(self):
            return 1

        def nth(self, index):
            return self

        async def is_visible(self):
            return True

        async def click(self):
            raise RuntimeError("detached DOM")

    class Root:
        def locator(self, selector):
            return Locator()

    class Browser:
        async def capture_debug(self, *args, **kwargs):
            return None

    provider = DouyinPlaywrightProvider(browser_manager=Browser())
    with pytest.raises(DouyinPageParseError, match="控件点击失败"):
        await provider._click_optional(Root(), "comment.load_more", page=object(), max_clicks=1)


def test_reply_policy_rejects_unsafe_configuration():
    with pytest.raises(ValueError, match="不能重复"):
        ReplyPolicyIn(allowed_intents=["price"], blocked_intents=["price"])
    with pytest.raises(ValueError, match="不能开启自动回复"):
        ReplyPolicyIn(enabled=False, auto_reply_enabled=True)
    with pytest.raises(ValueError):
        ReplyPolicyIn(minimum_interval_seconds=0)


def test_reply_policy_rejects_synthetic_comment_identity():
    db = _reply_test_session()
    comment = _reply_test_comment(db)
    comment.id_source = "fingerprint"

    with pytest.raises(HTTPException) as blocked:
        enforce_send_policy(db, comment)

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "COMMENT_ID_NOT_SENDABLE"


def test_scan_auto_reply_gate_requires_explicit_policy_and_lead_thresholds():
    from app.services.radar_service import RadarService

    lead = Lead(confidence=0.92, lead_score=88, intent_level="high")
    policy = ReplyPolicy(enabled=True, auto_reply_enabled=True, minimum_confidence=0.8, minimum_lead_score=80, allowed_intents=["high"])
    assert RadarService._auto_reply_eligible(policy, lead) is True
    assert RadarService._auto_reply_eligible(None, lead) is False
    policy.auto_reply_own_content_only = True
    assert RadarService._auto_reply_eligible(policy, lead) is False
    policy.auto_reply_own_content_only = False
    lead.lead_score = 50
    assert RadarService._auto_reply_eligible(policy, lead) is False


class _ReplyProvider:
    name = "Douyin Playwright"

    def __init__(self, status: ReplyStatus):
        self.status = status
        self.calls = 0
        self.verify_calls = 0

    async def reply_comment(self, video_url, comment, text):
        self.calls += 1
        return ReplyResult(self.status, "douyin", video_url, comment.comment_id, text, self.status is ReplyStatus.VERIFIED)

    async def verify_reply(self, video_url, comment, text):
        self.verify_calls += 1
        return ReplyResult(self.status, "douyin", video_url, comment.comment_id, text, self.status is ReplyStatus.VERIFIED)


def _reply_test_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _reply_test_comment(db):
    project = Project(name="测试项目", industry="装修")
    db.add(project)
    db.flush()
    video = Video(project_id=project.id, platform_video_id="video-1", title="真实视频", keyword="装修")
    db.add(video)
    db.flush()
    comment = Comment(
        project_id=project.id,
        video_id=video.id,
        platform_comment_id="comment-1",
        platform_user_id="user-1",
        nickname="客户",
        content="大概多少钱？",
        content_hash="comment-hash",
    )
    db.add(comment)
    db.commit()
    return comment


def test_direct_agent_run_failure_is_not_recorded_as_success_without_llm_call():
    from app import main

    db = _reply_test_session()
    project = Project(name="Agent 失败审计项目", industry="装修")
    db.add(project)
    db.commit()
    llm = OpenAICompatibleProvider(Settings(llm_base_url="", llm_api_key="", llm_model="text-model"))

    main._agent_run_from_call(
        db,
        project.id,
        "LeadJudgeAgent",
        "lead_judge_text_v1",
        {"comment": "测试"},
        {},
        llm,
        success=False,
        error="LLM_NOT_CONFIGURED",
    )
    db.commit()

    run = db.scalar(select(AgentRun).where(AgentRun.project_id == project.id))
    assert run is not None
    assert run.success is False
    assert run.error == "LLM_NOT_CONFIGURED"


def test_analytics_reports_partial_comment_coverage_without_claiming_complete():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    comment.coverage_status = "partial"
    db.commit()

    result = main.analytics(comment.project_id, db)

    assert result["health"]["comment_coverage_status"] == "partial"
    assert result["health"]["comment_coverage"] == 50


def test_comment_list_includes_video_and_lead_context():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    comment.coverage_status = "partial"
    db.commit()

    rows = main.list_comments(comment.project_id, 100, db)

    assert rows[0]["video_title"] == "真实视频"
    assert rows[0]["keyword"] == "装修"
    assert rows[0]["video_url"] == ""
    assert rows[0]["reply_status"] is None
    assert rows[0]["lead_id"] is None


def test_knowledge_mutations_require_the_current_project_scope():
    from app import main

    db = _reply_test_session()
    project = Project(name="知识项目", industry="装修")
    other = Project(name="其他项目", industry="教育")
    db.add_all([project, other])
    db.flush()
    entry = KnowledgeEntry(project_id=project.id, title="报价规则", content="先确认面积")
    db.add(entry)
    db.commit()

    with pytest.raises(HTTPException) as wrong_update:
        main.update_knowledge(entry.id, main.KnowledgeIn(title="越权", content="不应修改"), project_id=other.id, db=db)
    assert wrong_update.value.status_code == 404
    db.refresh(entry)
    assert entry.title == "报价规则"

    updated = main.update_knowledge(entry.id, main.KnowledgeIn(title="报价规则（更新）", content="先确认面积和预算"), project_id=project.id, db=db)
    assert updated.title == "报价规则（更新）"

    with pytest.raises(HTTPException) as wrong_delete:
        main.delete_knowledge(entry.id, project_id=other.id, db=db)
    assert wrong_delete.value.status_code == 404
    assert db.get(KnowledgeEntry, entry.id) is not None

    assert main.delete_knowledge(entry.id, project_id=project.id, db=db) == {"deleted": entry.id}
    assert db.get(KnowledgeEntry, entry.id) is None


@pytest.mark.asyncio
async def test_comment_and_reply_actions_require_the_current_project_scope():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    other = Project(name="其他项目", industry="教育")
    db.add(other)
    db.flush()
    reply = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="先确认需求", status="WAITING_REVIEW")
    db.add(reply)
    db.commit()

    with pytest.raises(HTTPException) as wrong_comment:
        main.get_comment(comment.id, project_id=other.id, db=db)
    assert wrong_comment.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_review:
        main.review_reply(reply.id, main.ReplyReviewIn(action="approve"), project_id=other.id, db=db)
    assert wrong_review.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_send:
        await main.send_comment_reply(
            comment.id,
            ReplyActionIn(reply_text="不应发送", confirm=True),
            project_id=other.id,
            db=db,
        )
    assert wrong_send.value.status_code == 404


def test_lead_and_follow_task_actions_require_the_current_project_scope():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    lead = Lead(project_id=comment.project_id, nickname="客户", lead_score=80, lead_level="A")
    other = Project(name="其他项目", industry="教育")
    db.add_all([lead, other])
    db.commit()

    with pytest.raises(HTTPException) as wrong_detail:
        main.get_lead(lead.id, db=db, project_id=other.id)
    assert wrong_detail.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_update:
        main.update_lead(lead.id, main.LeadStatusUpdate(status="CONTACTED"), db=db, project_id=other.id)
    assert wrong_update.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_follow_task:
        main.create_follow_task(
            lead.id,
            main.FollowTaskCreate(content="不应创建", deadline=now_utc() + timedelta(hours=1)),
            db=db,
            project_id=other.id,
        )
    assert wrong_follow_task.value.status_code == 404


@pytest.mark.asyncio
async def test_collection_and_task_actions_require_the_current_project_scope():
    from app import main
    from app.models import Keyword

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    keyword = Keyword(project_id=comment.project_id, keyword="装修报价", category="购买意向")
    video = db.get(Video, comment.video_id)
    task = ScanTask(project_id=comment.project_id, name="扫描", status="paused")
    other = Project(name="其他项目", industry="教育")
    db.add_all([keyword, task, other])
    db.commit()

    with pytest.raises(HTTPException) as wrong_keyword:
        main.get_keyword(keyword.id, db=db, project_id=other.id)
    assert wrong_keyword.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_video:
        main.get_video(video.id, db=db, project_id=other.id)
    assert wrong_video.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_task:
        main.get_task(task.id, db=db, project_id=other.id)
    assert wrong_task.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_transition:
        main.pause_task(task.id, db=db, project_id=other.id)
    assert wrong_transition.value.status_code == 404


@pytest.mark.asyncio
async def test_manual_reply_requires_confirm_and_blocks_repeat(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    provider = _ReplyProvider(ReplyStatus.VERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)
    payload = ReplyActionIn(reply_text="可以先根据面积和需求估算。", confirm=True)

    with pytest.raises(HTTPException) as missing_confirm:
        await main.send_comment_reply(comment.id, ReplyActionIn(reply_text="不应发送"), db)
    assert missing_confirm.value.status_code == 400
    assert provider.calls == 0

    result = await send_comment_reply(comment.id, payload, db)
    assert result["reply"].status == "VERIFIED"
    assert provider.calls == 1

    with pytest.raises(HTTPException) as repeated:
        await send_comment_reply(comment.id, payload, db)
    assert repeated.value.status_code == 409
    assert repeated.value.detail["code"] == "REPLY_ALREADY_SENT"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_manual_reply_cannot_change_approved_text_at_send_boundary(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    approved = CommentReply(
        project_id=comment.project_id,
        comment_id=comment.id,
        reply_text="已审核的回复",
        status="APPROVED",
        approved_at=now_utc(),
    )
    db.add(approved)
    db.commit()
    provider = _ReplyProvider(ReplyStatus.VERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    with pytest.raises(HTTPException) as mismatch:
        await send_comment_reply(comment.id, ReplyActionIn(reply_text="绕过审核的新回复", confirm=True), db)

    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "REPLY_APPROVED_TEXT_MISMATCH"
    assert provider.calls == 0
    db.refresh(approved)
    assert approved.reply_text == "已审核的回复"


@pytest.mark.asyncio
async def test_manual_comment_sync_reconciles_existing_dom_record(monkeypatch):
    from app import main
    from app.providers.base import CommentScanResult
    from app.providers.douyin.dto import DouyinCommentDTO

    db = _reply_test_session()
    comment = _reply_test_comment(db)

    class Provider:
        async def get_comments(self, video_id, cursor=None):
            return CommentScanResult(
                items=[
                    DouyinCommentDTO(
                        platform="douyin",
                        comment_id=comment.platform_comment_id,
                        user_id="user-2",
                        nickname="新昵称",
                        profile_url="https://www.douyin.com/user/user-2",
                        content="更新后的评论文本",
                        parent_comment_id="parent-1",
                        is_reply=True,
                        like_count=12,
                        id_source="dom_attribute",
                        comment_url="https://www.douyin.com/comment/comment-1",
                    )
                ],
                coverage_status="partial",
                items_received=1,
            )

    monkeypatch.setattr(main, "active_provider", lambda _db: Provider())
    video = db.get(Video, comment.video_id)
    result = await main.sync_douyin_comments(video.id, limit=None, db=db)

    db.refresh(comment)
    assert result["created"] == 0
    assert result["updated"] == 1
    assert comment.content == "更新后的评论文本"
    assert comment.platform_user_id == "user-2"
    assert comment.nickname == "新昵称"
    assert comment.parent_comment_id == "parent-1"
    assert comment.is_reply is True
    assert comment.like_count == 12
    assert comment.coverage_status == "partial"


@pytest.mark.asyncio
async def test_manual_comment_sync_can_consume_all_comment_pages(monkeypatch):
    from app import main
    from app.providers.base import CommentScanResult
    from app.providers.douyin.dto import DouyinCommentDTO

    db = _reply_test_session()
    comment = _reply_test_comment(db)

    class Provider:
        capabilities = {"comments": True}

        def __init__(self):
            self.cursors = []

        async def get_comments(self, video_id, cursor=None):
            self.cursors.append(cursor)
            if cursor is None:
                return CommentScanResult(
                    items=[DouyinCommentDTO("douyin", "comment-page-1", "user-2", "客户2", "", "第一页")],
                    coverage_status="partial",
                    items_received=1,
                    next_cursor="page-2",
                    has_more=True,
                )
            return CommentScanResult(
                items=[DouyinCommentDTO("douyin", "comment-page-2", "user-3", "客户3", "", "第二页")],
                coverage_status="complete",
                items_received=1,
            )

    provider = Provider()
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    async def analyze(self, db, video, comments):
        return {"status": "SUCCESS", "analyzed": len(comments)}

    monkeypatch.setattr(main.RadarService, "analyze_video_comments", analyze)
    result = await main.sync_douyin_comments(comment.video_id, all_pages=True, db=db)

    assert provider.cursors == [None, "page-2"]
    assert result["pages"] == 2
    assert result["received"] == 2
    assert result["created"] == 2
    assert result["coverage_status"] == "partial"
    assert result["next_cursor"] is None
    assert result["has_more"] is False
    assert result["analysis"] == {"status": "SUCCESS", "analyzed": 2}
    assert db.scalar(select(Comment).where(Comment.platform_comment_id == "comment-page-2")) is not None


@pytest.mark.asyncio
async def test_manual_comment_sync_runs_text_lead_pipeline(monkeypatch):
    from app import main
    from app.providers.base import CommentScanResult
    from app.providers.douyin.dto import DouyinCommentDTO

    class TextLLM(BaseLLMProvider):
        configured = True
        model = "manual-sync-text-test"

        async def structured_output(self, system, user, schema):
            self.last_call = LLMCall(self.model, user, {}, tokens=5, latency_ms=1)
            return {
                "is_lead": True,
                "confidence": 0.95,
                "lead_score": 92,
                "intent_level": "high",
                "need": "装修报价",
                "location": "长沙",
                "budget": "10万",
                "time_requirement": "年底",
                "purchase_stage": "准备咨询",
                "pain_point": "担心增项",
                "buying_signals": ["询价", "明确时间"],
                "summary": "用户有明确装修计划",
                "reason": "评论包含预算和时间",
                "recommended_action": "人工跟进",
                "should_reply": True,
            }

    db = _reply_test_session()
    comment = _reply_test_comment(db)

    class Provider:
        async def get_comments(self, video_id, cursor=None):
            return CommentScanResult(
                items=[DouyinCommentDTO("douyin", "comment-2", "user-2", "客户2", "", "长沙120平大概多少钱？", id_source="dom_attribute")],
                coverage_status="partial",
                items_received=1,
            )

    monkeypatch.setattr(main, "active_provider", lambda _db: Provider())
    monkeypatch.setattr(main, "active_llm", lambda _db: TextLLM())
    result = await main.sync_douyin_comments(comment.video_id, limit=None, db=db)

    assert result["analysis"]["status"] == "SUCCESS"
    assert result["analysis"]["analyzed"] == 1
    assert result["analysis"]["leads"] == 1
    assert db.scalar(select(Lead).where(Lead.project_id == comment.project_id, Lead.nickname == "客户2")) is not None


@pytest.mark.asyncio
async def test_video_scan_route_is_scoped_to_one_video(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    called = {}

    async def sync(video_id, limit, cursor, all_pages, db):
        called.update(video_id=video_id, limit=limit, cursor=cursor, session=db)
        called["all_pages"] = all_pages
        return {"video_id": video_id, "analysis": {"status": "SUCCESS"}}

    monkeypatch.setattr(main, "sync_douyin_comments", sync)
    result = await main.scan_video(comment.video_id, db)

    assert result["video_id"] == comment.video_id
    assert called == {"video_id": comment.video_id, "limit": None, "cursor": None, "session": db, "all_pages": True}


def test_lead_follow_task_and_note_are_durable():
    from app import main
    from app.main import FollowTaskCreate, FollowTaskUpdate, LeadStatusUpdate
    from app.models import FollowTask

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    lead = Lead(project_id=comment.project_id, platform="douyin", platform_user_id="user-follow", nickname="待跟进客户")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    updated = main.update_lead(lead.id, LeadStatusUpdate(follow_note="先确认面积和预算"), db)
    follow_task = main.create_follow_task(
        lead.id,
        FollowTaskCreate(content="明天 10 点跟进报价", deadline=datetime(2026, 9, 18, 2, 0)),
        db,
    )
    completed = main.update_follow_task(follow_task.id, FollowTaskUpdate(status="DONE"), db)

    assert updated.follow_note == "先确认面积和预算"
    assert completed.status == "DONE"
    assert completed.completed_at is not None
    assert db.scalar(select(FollowTask).where(FollowTask.lead_id == lead.id)).id == follow_task.id


@pytest.mark.asyncio
async def test_manual_reply_respects_disabled_project_policy(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    db.add(ReplyPolicy(project_id=comment.project_id, enabled=False))
    db.commit()
    provider = _ReplyProvider(ReplyStatus.VERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    with pytest.raises(HTTPException) as blocked:
        await send_comment_reply(comment.id, ReplyActionIn(reply_text="不应发送", confirm=True), db)
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "REPLY_POLICY_DISABLED"
    assert provider.calls == 0


def test_reply_policy_applies_interval_limit():
    db = _reply_test_session()
    comment = _reply_test_comment(db)
    db.add(CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="上一条", status="VERIFIED", sent_at=now_utc()))
    db.add(ReplyPolicy(project_id=comment.project_id, minimum_interval_seconds=30))
    db.commit()
    with pytest.raises(HTTPException) as blocked:
        enforce_send_policy(db, comment)
    assert blocked.value.detail["code"] == "REPLY_INTERVAL_LIMIT"


def test_expired_sending_is_recovered_to_failed_and_can_be_reviewed_again():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    checked_at = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
    reply = CommentReply(
        project_id=comment.project_id,
        comment_id=comment.id,
        reply_text="先确认面积",
        status="SENDING",
        attempt_count=2,
        sending_started_at=checked_at - timedelta(minutes=6),
        send_lease_expires_at=checked_at - timedelta(seconds=1),
    )
    db.add(reply)
    db.commit()

    assert recover_stale_sending(db, now=checked_at) == 1
    db.commit()
    assert reply.status == "FAILED"
    assert reply.error_code == "SENDING_EXPIRED"
    assert reply.send_lease_expires_at is None
    assert reply.attempt_count == 2

    retried = main.review_reply(reply.id, main.ReplyReviewIn(action="retry"), db)
    assert retried.status == "WAITING_REVIEW"
    assert retried.attempt_count == 2


def test_legacy_sending_without_lease_uses_updated_at_fallback():
    db = _reply_test_session()
    comment = _reply_test_comment(db)
    checked_at = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
    reply = CommentReply(
        project_id=comment.project_id,
        comment_id=comment.id,
        reply_text="旧记录",
        status="SENDING",
        updated_at=checked_at - timedelta(minutes=6),
    )
    db.add(reply)
    db.commit()

    assert recover_stale_sending(db, now=checked_at, comment_id=comment.id) == 1
    db.commit()
    assert reply.status == "FAILED"
    assert reply.error_code == "SENDING_EXPIRED"


def test_sent_unverified_reverification_is_a_single_audited_record():
    db = _reply_test_session()
    comment = _reply_test_comment(db)
    sent_at = datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)
    first_check = sent_at + timedelta(minutes=1)
    second_check = sent_at + timedelta(minutes=2)
    reply = CommentReply(
        project_id=comment.project_id,
        comment_id=comment.id,
        reply_text="先确认需求",
        status="SENT_UNVERIFIED",
        sent_at=sent_at,
    )
    db.add(reply)
    db.commit()

    record_reply_verification(
        db,
        reply.id,
        verified=False,
        checked_at=first_check,
        platform_reply_id="platform-reply-1",
        error_code="DOM_NOT_FOUND",
        error_message="本次页面检查未找到回复",
    )
    db.commit()
    assert reply.status == "SENT_UNVERIFIED"
    assert reply.verification_attempt_count == 1
    assert reply.last_verification_at == first_check
    assert reply.platform_reply_id == "platform-reply-1"
    assert reply.verification_error_code == "DOM_NOT_FOUND"

    record_reply_verification(db, reply.id, verified=True, checked_at=second_check)
    db.commit()
    assert reply.status == "VERIFIED"
    assert reply.verification_attempt_count == 2
    assert reply.verified_at == second_check
    assert reply.verification_error_code == ""
    assert db.query(CommentReply).filter(CommentReply.comment_id == comment.id).count() == 1


def test_sqlite_allows_one_sending_claim_per_comment_but_keeps_history():
    db = _reply_test_session()
    comment = _reply_test_comment(db)
    first = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="第一次", status="SENDING")
    db.add(first)
    db.commit()

    duplicate = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="重复", status="SENDING")
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(CommentReply).filter(CommentReply.comment_id == comment.id, CommentReply.status == "SENDING").count() == 1

    first.status = "FAILED"
    db.commit()
    second = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="重试", status="SENDING")
    db.add(second)
    db.commit()
    assert db.query(CommentReply).filter(CommentReply.comment_id == comment.id, CommentReply.status == "SENDING").count() == 1


@pytest.mark.asyncio
async def test_manual_reply_persists_sent_unverified_status(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    provider = _ReplyProvider(ReplyStatus.SENT_UNVERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    result = await main.send_comment_reply(comment.id, ReplyActionIn(reply_text="先确认具体需求。", confirm=True), db)
    assert result["reply"].status == "SENT_UNVERIFIED"
    stored = db.scalar(select(CommentReply).where(CommentReply.comment_id == comment.id))
    assert stored.status == "SENT_UNVERIFIED"
    assert stored.verified_at is None


@pytest.mark.asyncio
async def test_sent_unverified_reply_can_be_reconciled_without_resending(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    provider = _ReplyProvider(ReplyStatus.SENT_UNVERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)
    sent = await main.send_comment_reply(
        comment.id,
        ReplyActionIn(reply_text="先确认具体需求。", confirm=True),
        db,
    )
    reply = sent["reply"]
    provider.status = ReplyStatus.VERIFIED

    verified = await main.verify_reply(reply.id, db)

    assert verified["reply"].status == "VERIFIED"
    assert provider.calls == 1
    assert provider.verify_calls == 1
    assert db.scalar(select(CommentReply).where(CommentReply.id == reply.id)).status == "VERIFIED"


@pytest.mark.asyncio
async def test_failed_reply_cannot_create_a_duplicate_send_record(monkeypatch):
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    failed = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="上一条", status="FAILED")
    db.add(failed)
    db.commit()
    provider = _ReplyProvider(ReplyStatus.VERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    with pytest.raises(HTTPException) as blocked:
        await main.send_comment_reply(comment.id, ReplyActionIn(reply_text="重试", confirm=True), db)

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "REPLY_RETRY_REQUIRES_REVIEW"
    assert provider.calls == 0
    assert db.query(CommentReply).filter(CommentReply.comment_id == comment.id).count() == 1


@pytest.mark.asyncio
async def test_batch_reply_requires_confirmation_and_keeps_item_results(monkeypatch):
    from app import main

    db = _reply_test_session()
    first = _reply_test_comment(db)
    second = _reply_test_comment(db)
    provider = _ReplyProvider(ReplyStatus.VERIFIED)
    monkeypatch.setattr(main, "active_provider", lambda _db: provider)

    with pytest.raises(HTTPException) as missing_confirm:
        await main.send_comment_reply_batch(ReplyBatchIn(items=[{"comment_id": first.id, "reply_text": "先确认需求"}]), db)
    assert missing_confirm.value.status_code == 400
    assert provider.calls == 0

    result = await main.send_comment_reply_batch(
        ReplyBatchIn(
            items=[
                {"comment_id": first.id, "reply_text": "先确认需求"},
                {"comment_id": second.id, "reply_text": "可以进一步沟通"},
                {"comment_id": 999999, "reply_text": "不存在的评论"},
            ],
            confirm=True,
        ),
        db,
    )
    assert result["success_count"] == 2
    assert result["failed_count"] == 1
    assert provider.calls == 2
    assert [item["ok"] for item in result["results"]] == [True, True, False]


def test_reply_review_transitions_do_not_contact_provider():
    from app import main

    db = _reply_test_session()
    comment = _reply_test_comment(db)
    reply = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="先确认面积和预算", status="WAITING_REVIEW")
    db.add(reply)
    db.commit()

    approved = main.review_reply(reply.id, main.ReplyReviewIn(action="approve"), db)
    assert approved.status == "APPROVED"
    assert approved.approved_at is not None

    skipped = main.review_reply(reply.id, main.ReplyReviewIn(action="skip"), db)
    assert skipped.status == "SKIPPED"
    assert skipped.error_code == "MANUAL_SKIPPED"

    failed = CommentReply(project_id=comment.project_id, comment_id=comment.id, reply_text="重试", status="FAILED", error_code="DOUYIN_REPLY_FAILED", error_message="页面失败")
    db.add(failed)
    db.commit()
    retried = main.review_reply(failed.id, main.ReplyReviewIn(action="retry"), db)
    assert retried.status == "WAITING_REVIEW"
    assert retried.error_code == ""
