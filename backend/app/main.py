import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import desc, func, or_, select, text, update
from sqlalchemy.orm import Session, aliased

from app.agents.llm import OpenAICompatibleProvider, input_hash, settings_with_db
from app.errors import LLMError
from app.agents.persona_agent import PersonaAgent
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.reply_agent import ReplyAgent
from app.agents.radar_agent import RadarAgent
from app.core.config import get_settings
from app.db import SessionLocal, get_db
from app.models import AgentRun, BrowserProfile, Comment, CommentReply, DouyinAccount, Keyword, KnowledgeEntry, Lead, LeadComment, LeadEvent, LeadSource, Persona, Project, ProviderRecord, ReplyPolicy, ScanSchedule, ScanTask, Setting, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, Video, now_utc
from app.providers.douyin.dto import DouyinCommentDTO, LoginStatus, ReplyStatus
from app.providers.douyin.exceptions import DouyinError
from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider
from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.base import BaseContentProvider
from app.seed import init_database
from app.security import auth_middleware
from app.settings_store import encrypt_secret, read_setting
from app.services.event_bus import event_bus, sse_line
from app.services.radar_service import RadarService, _aggregate_coverage_status, _upsert_lead
from app.services.reply_policy import DEFAULT_SENDING_LEASE_SECONDS, enforce_send_policy, record_reply_verification, recover_stale_sending
from app.tasks.queue import claim_next_task
from app.tasks.scheduler import create_scheduler, enqueue_due_schedules


class ProjectCreate(BaseModel):
    name: str = Field(default="我的行业雷达", min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    location: str = ""
    service: str = ""
    price_range: str = ""
    target_customer: str = ""
    description: str = ""


class ProjectOut(ProjectCreate):
    id: int
    status: str
    intelligence: dict = Field(default_factory=dict)
    model_config = ConfigDict(from_attributes=True)


class ScheduleIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=30, ge=10, le=30)
    full: bool = False
    next_run_at: datetime | None = None


class ScheduleOut(BaseModel):
    id: int | None
    project_id: int
    enabled: bool
    interval_minutes: int
    full: bool
    next_run_at: str | None
    last_run_at: str | None


class LeadStatusUpdate(BaseModel):
    status: Literal["NEW", "FOLLOW_UP", "CONTACTED", "QUALIFIED", "WON", "LOST", "IGNORED"]


class PersonaIn(BaseModel):
    name: str
    identity: str = ""
    experience: str = ""
    location: str = ""
    tone: str = "专业但不推销"
    strengths: str = ""
    forbidden_words: str = ""
    sample_reply: str = ""


class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class ReplyPolicyIn(BaseModel):
    enabled: bool = True
    auto_reply_enabled: bool = False
    minimum_confidence: float = Field(default=0.8, ge=0, le=1)
    minimum_lead_score: float = Field(default=70, ge=0, le=100)
    allowed_intents: list[str] = Field(default_factory=list)
    blocked_intents: list[str] = Field(default_factory=list)
    max_replies_per_hour: int = Field(default=10, ge=0, le=1000)
    max_replies_per_day: int = Field(default=50, ge=0, le=10000)
    minimum_interval_seconds: int = Field(default=30, ge=1, le=86400)
    auto_reply_own_content_only: bool = False

    @model_validator(mode="after")
    def validate_safety(self):
        overlap = sorted(set(self.allowed_intents) & set(self.blocked_intents))
        if overlap:
            raise ValueError(f"allowed_intents 与 blocked_intents 不能重复: {', '.join(overlap)}")
        if self.auto_reply_enabled and not self.enabled:
            raise ValueError("禁用回复策略时不能开启自动回复")
        return self


class GenerateReplyIn(BaseModel):
    reply_text: str | None = None


class ReplyReviewIn(BaseModel):
    action: Literal["approve", "skip", "retry"]
    reply_text: str | None = Field(default=None, max_length=1000)


class ReplyActionIn(BaseModel):
    reply_text: str = Field(min_length=1, max_length=1000)
    confirm: bool = False


class ReplyBatchItem(BaseModel):
    comment_id: int
    reply_text: str = Field(min_length=1, max_length=1000)


class ReplyBatchIn(BaseModel):
    items: list[ReplyBatchItem] = Field(min_length=1, max_length=50)
    confirm: bool = False


class SettingsInput(BaseModel):
    """LLM settings accepted from both env-compatible forms and the UI."""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float | str | None = None
    llm_timeout: float | str | None = None


_douyin_provider: DouyinPlaywrightProvider | None = None
_crawler_provider: DouyinCommentsCrawlerExternalProvider | None = None
_reply_send_locks: dict[int, asyncio.Lock] = {}


def provider_registry(settings=None):
    settings = settings or get_settings()
    global _douyin_provider, _crawler_provider
    if _douyin_provider is None:
        _douyin_provider = DouyinPlaywrightProvider(
            profile_dir=settings.douyin_profile_dir,
            browser_channel=settings.douyin_browser_channel,
            headless=settings.douyin_headless,
            proxy_server=settings.douyin_proxy_server,
        )
    if _crawler_provider is None or _crawler_provider.base_url != settings.douyin_comments_crawler_url.rstrip("/"):
        _crawler_provider = DouyinCommentsCrawlerExternalProvider(settings.douyin_comments_crawler_url, timeout=settings.llm_timeout)
    return [_douyin_provider, _crawler_provider]


def active_provider(db: Session | None = None):
    settings = get_settings()
    selected = settings.content_provider
    if db:
        stored = db.get(Setting, "content_provider")
        selected = stored.value if stored else selected
    providers = provider_registry(settings)
    aliases = {
        "douyin-playwright": "Douyin Playwright",
        "douyin-comments-crawler": "Douyin Comments Crawler",
    }
    selected_name = aliases.get(selected, selected)
    provider = next((provider for provider in providers if provider.name == selected_name), None)
    if provider is None:
        raise HTTPException(503, {"code": "PROVIDER_NOT_CONFIGURED", "message": f"不支持的数据源配置: {selected}", "detail": {"allowed": sorted(aliases)}})
    return provider


def _require_playwright_provider(provider: BaseContentProvider) -> DouyinPlaywrightProvider:
    if not isinstance(provider, DouyinPlaywrightProvider):
        raise HTTPException(400, "当前 Provider 不支持抖音浏览器登录操作，请先激活 Douyin Playwright")
    return provider


def active_llm(db: Session) -> OpenAICompatibleProvider:
    settings = get_settings()
    values = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    return OpenAICompatibleProvider(settings_with_db(settings, values))


def _sync_douyin_account(db: Session, provider: DouyinPlaywrightProvider, status: LoginStatus | None):
    """Persist browser/profile state without storing cookies or credentials."""
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.name == "默认抖音账号"))
    if account is None:
        account = DouyinAccount(name="默认抖音账号", profile_dir=str(provider.browser.profile_dir))
        db.add(account)
        db.flush()
    previous_status = account.status
    account.profile_dir = str(provider.browser.profile_dir)
    account.status = status.value if provider.browser.is_running and status is not None else "BROWSER_STOPPED"
    account.last_checked_at = now_utc()
    if status is LoginStatus.LOGGED_IN and previous_status != LoginStatus.LOGGED_IN.value:
        account.last_login_at = account.last_checked_at

    profile = db.scalar(
        select(BrowserProfile).where(
            BrowserProfile.account_id == account.id,
            BrowserProfile.profile_dir == str(provider.browser.profile_dir),
        )
    )
    if profile is None:
        profile = BrowserProfile(
            account_id=account.id,
            name="默认抖音浏览器 Profile",
            profile_dir=str(provider.browser.profile_dir),
            browser_channel=provider.browser.channel,
            headless=provider.browser.headless,
        )
        db.add(profile)
    profile.browser_channel = provider.browser.channel
    profile.headless = provider.browser.headless
    profile.status = "ACTIVE" if provider.browser.is_running else "INACTIVE"
    if provider.browser.is_running:
        profile.last_used_at = now_utc()
    db.commit()
    db.refresh(account)
    return account


async def _task_worker(stop: asyncio.Event):
    while not stop.is_set():
        claimed = None
        try:
            with SessionLocal() as db:
                claimed = claim_next_task(db)
                if claimed:
                    task_id, full = claimed
                    try:
                        provider = active_provider(db)
                        llm = active_llm(db)
                    except Exception as exc:
                        # A task is already durable and marked running when
                        # runtime dependencies are resolved.  Do not let a
                        # bad Provider/LLM configuration terminate the only
                        # worker; leave an actionable terminal task state.
                        task = db.get(ScanTask, task_id)
                        if task is not None and task.status == "running":
                            task.status = "failed"
                            task.error = f"运行时初始化失败：{exc}"
                            task.finished_at = now_utc()
                            db.commit()
                        claimed = None
            if claimed:
                try:
                    await RadarService(provider, llm).run_task(task_id, full=full)
                except Exception as exc:
                    # RadarService owns normal task failures, but an
                    # unexpected boundary error must not kill queue progress.
                    _mark_worker_task_failed(task_id, exc)
                continue
        except Exception as exc:
            # Database/queue errors are transient infrastructure failures. If
            # a task was claimed, make a best-effort terminal transition; the
            # startup/watchdog recovery remains the safety net if the DB is
            # unavailable for that transition.
            if claimed:
                _mark_worker_task_failed(claimed[0], exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass


def _mark_worker_task_failed(task_id: int, exc: Exception) -> None:
    with SessionLocal() as db:
        task = db.get(ScanTask, task_id)
        if task is not None and task.status == "running":
            task.status = "failed"
            task.error = f"Worker 执行异常：{exc}"
            task.finished_at = now_utc()
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    with SessionLocal() as db:
        db.execute(update(ScanTask).where(ScanTask.status == "running").values(status="queued", error=""))
        for provider in provider_registry():
            record = db.scalar(select(ProviderRecord).where(ProviderRecord.name == provider.name))
            is_browser = isinstance(provider, DouyinPlaywrightProvider)
            provider_kind = "browser" if is_browser else "external_http"
            provider_endpoint = getattr(provider, "home_url", getattr(provider, "base_url", ""))
            provider_note = "通过 Playwright DOM 读取抖音公开文本；登录需要在真实浏览器中完成。" if is_browser else "通过外部 douyin-comments-crawler HTTP 服务读取真实公开内容；服务不可用时不会生成替代数据。"
            if not record:
                db.add(ProviderRecord(name=provider.name, kind=provider_kind, status="disconnected", platform=provider.platform, capabilities=provider.capabilities, endpoint=provider_endpoint, note=provider_note))
            else:
                # Capabilities are a code contract, not user-edited state.
                # Refresh them so a provider change cannot leave stale UI data.
                record.kind = provider_kind
                record.platform = provider.platform
                record.capabilities = provider.capabilities
                record.endpoint = provider_endpoint
                record.note = provider_note
        db.commit()
    scheduler = create_scheduler()
    def resolve_scheduled_provider():
        with SessionLocal() as db:
            return active_provider(db)

    scheduler.add_job(
        enqueue_due_schedules,
        "interval",
        minutes=1,
        kwargs={"provider_resolver": resolve_scheduled_provider},
        id="scan-schedules",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    worker_stop = asyncio.Event()
    worker = asyncio.create_task(_task_worker(worker_stop))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        worker_stop.set()
        await worker
        if _douyin_provider is not None:
            await _douyin_provider.close()


app = FastAPI(title="AI 截流雷达", version="0.1.0", lifespan=lifespan)
app.middleware("http")(auth_middleware)
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    return JSONResponse(status_code=502, content={"code": exc.code, "message": exc.message, "detail": {}})


@app.exception_handler(DouyinError)
async def douyin_error_handler(request: Request, exc: DouyinError):
    return JSONResponse(status_code=409 if exc.code in {"DOUYIN_LOGIN_REQUIRED", "DOUYIN_VERIFICATION_REQUIRED", "DOUYIN_LOGIN_EXPIRED", "DOUYIN_COMMENT_AMBIGUOUS"} else 502, content={"code": exc.code, "message": exc.message, "detail": exc.detail})


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or f"HTTP_{exc.status_code}")
        message = str(detail.get("message") or detail.get("detail") or "请求失败")
        payload_detail = detail.get("detail", {})
    else:
        raw = str(detail or "请求失败")
        code, separator, message = raw.partition(":")
        if not separator or not code.isupper():
            code, message = f"HTTP_{exc.status_code}", raw
        payload_detail = {}
    return JSONResponse(status_code=exc.status_code, content={"code": code, "message": message.strip(), "detail": payload_detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"code": "REQUEST_VALIDATION_FAILED", "message": "请求参数校验失败", "detail": jsonable_encoder(exc.errors())})


@app.get("/health")
async def health(db: Session = Depends(get_db)):
    provider = active_provider(db)
    health_status = "running"
    if isinstance(provider, DouyinPlaywrightProvider):
        try:
            await provider.ensure_browser_started()
            login = (await provider.get_login_status()).value
            douyin = {"browser": "running" if provider.browser.is_running else "stopped", "login": login}
        except DouyinError as exc:
            # Liveness must remain observable when Douyin is temporarily
            # unreachable. Scans still surface the provider error, while the
            # probe reports a truthful degraded dependency instead of a 5xx.
            health_status = "degraded"
            douyin = {
                "browser": "running" if provider.browser.is_running else "stopped",
                "login": "unavailable",
                "error_code": exc.code,
                "error": exc.message,
            }
    else:
        provider_health = await provider.health_check()
        if provider_health.status != "connected":
            health_status = "degraded"
        douyin = {"browser": "not_applicable", "login": "not_applicable", "crawler": provider_health.status}
    return {"status": health_status, "service": "AI 截流雷达", "version": "0.1.0", "database": "connected", "llm": "configured" if active_llm(db).configured else "not_configured", "provider": provider.name, "douyin": douyin}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    """Deployment readiness probe: verify the API can reach its database."""
    db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "connected"}


@app.get("/api/douyin/status")
async def douyin_status(db: Session = Depends(get_db)):
    provider = _require_playwright_provider(active_provider(db))
    # Re-open the persistent context after an API restart so the saved
    # Chromium session is checked instead of reporting NOT_STARTED and
    # sending the user through login again.
    await provider.ensure_browser_started()
    status = await provider.get_login_status()
    account = _sync_douyin_account(db, provider, status)
    return {"provider": provider.name, "browser": "running" if provider.browser.is_running else "stopped", "login": status.value, "profile_dir": str(provider.browser.profile_dir), "headless": provider.browser.headless, "account_id": account.id, "account_status": account.status, "last_checked_at": account.last_checked_at}


@app.post("/api/douyin/browser/start")
async def douyin_browser_start(db: Session = Depends(get_db)):
    provider = _require_playwright_provider(active_provider(db))
    await provider.start_browser()
    status = await provider.get_login_status()
    account = _sync_douyin_account(db, provider, status)
    return {"provider": provider.name, "browser": "running", "login": status.value, "account_id": account.id, "message": "请在打开的真实抖音浏览器中完成扫码登录" if status is not LoginStatus.LOGGED_IN else "抖音登录状态已确认"}


@app.post("/api/douyin/browser/close")
async def douyin_browser_close(db: Session = Depends(get_db)):
    provider = _require_playwright_provider(active_provider(db))
    await provider.close_browser()
    account = _sync_douyin_account(db, provider, None)
    return {"provider": provider.name, "browser": "stopped", "account_id": account.id, "account_status": account.status}


@app.get("/api/douyin/login/status")
async def douyin_login_status(db: Session = Depends(get_db)):
    provider = _require_playwright_provider(active_provider(db))
    await provider.ensure_browser_started()
    status = await provider.get_login_status()
    account = _sync_douyin_account(db, provider, status)
    return {"status": status.value, "browser": "running" if provider.browser.is_running else "stopped", "account_id": account.id, "account_status": account.status}


class DouyinSearchIn(BaseModel):
    project_id: int
    keyword: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


@app.post("/api/douyin/search")
async def douyin_search(payload: DouyinSearchIn, db: Session = Depends(get_db)):
    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    provider = active_provider(db)
    videos = await provider.search_videos(payload.keyword, payload.limit)
    radar = RadarAgent()
    output = []
    for dto in videos:
        scores = radar.score({"title": dto.title, "description": dto.description, "creator": dto.creator, "publish_time": dto.publish_time, "likes": dto.likes, "comments": dto.comments, "shares": dto.shares, "collects": dto.collects}, payload.keyword)
        video = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform == dto.platform, Video.platform_video_id == dto.video_id))
        video_values = {"opportunity_score": scores["video_opportunity_score"], "industry_relevance_score": scores["industry_relevance_score"], "commercial_relevance_score": scores["commercial_relevance_score"], "lead_opportunity_score": scores["lead_opportunity_score"], "level": scores["level"]}
        if video is None:
            video = Video(project_id=project.id, platform=dto.platform, platform_video_id=dto.video_id, title=dto.title, description=dto.description, creator=dto.creator, url=dto.url, cover=dto.cover, publish_time=dto.publish_time, likes=dto.likes, comments=dto.comments, shares=dto.shares, collects=dto.collects, keyword=payload.keyword, **video_values)
            db.add(video)
        else:
            for key in ("title", "description", "creator", "url", "cover", "publish_time", "likes", "comments", "shares", "collects"):
                setattr(video, key, getattr(dto, key))
            video.keyword = payload.keyword
            for key, value in video_values.items():
                setattr(video, key, value)
        output.append(video)
    db.commit()
    return output


@app.post("/api/douyin/videos/{video_id}/comments/sync")
async def sync_douyin_comments(video_id: int, limit: int | None = Query(None, ge=1, le=500), cursor: str | None = None, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "视频不存在")
    provider = active_provider(db)
    result = await provider.get_comments(video.platform_video_id, cursor=cursor)
    created = 0
    updated = 0
    for dto in result.items[: limit or get_settings().douyin_default_comment_limit]:
        existing = db.scalar(select(Comment).where(Comment.project_id == video.project_id, Comment.platform == dto.platform, Comment.platform_comment_id == dto.comment_id))
        if existing:
            # Repeated manual/scheduled syncs must reconcile mutable public
            # fields instead of treating the first observation as permanent.
            # Keep the latest task provenance untouched: this endpoint does
            # not create a ScanTask, while the resumable scan service owns
            # task/checkpoint associations.
            existing.video_id = video.id
            existing.platform_user_id = dto.user_id
            existing.id_source = getattr(dto, "id_source", existing.id_source)
            existing.nickname = dto.nickname
            existing.profile_url = dto.profile_url
            existing.comment_url = getattr(dto, "comment_url", existing.comment_url)
            existing.content = dto.content
            existing.content_hash = input_hash(dto.content)
            existing.parent_comment_id = dto.parent_comment_id
            existing.is_reply = getattr(dto, "is_reply", existing.is_reply)
            existing.like_count = getattr(dto, "like_count", existing.like_count)
            existing.created_at_platform = dto.created_at
            existing.coverage_status = result.coverage_status
            updated += 1
            continue
        db.add(Comment(project_id=video.project_id, video_id=video.id, platform=dto.platform, platform_comment_id=dto.comment_id, platform_user_id=dto.user_id, id_source=getattr(dto, "id_source", "dom_attribute"), nickname=dto.nickname, profile_url=dto.profile_url, comment_url=getattr(dto, "comment_url", ""), content=dto.content, content_hash=input_hash(dto.content), parent_comment_id=dto.parent_comment_id, is_reply=getattr(dto, "is_reply", False), like_count=getattr(dto, "like_count", 0), created_at_platform=dto.created_at, coverage_status=result.coverage_status))
        created += 1
    db.commit()
    return {"video_id": video.id, "received": result.items_received, "created": created, "updated": updated, "coverage_status": result.coverage_status, "next_cursor": result.next_cursor, "has_more": result.has_more}


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(desc(Project.updated_at))).all()


@app.post("/api/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(**payload.model_dump(), status="draft")
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return project


@app.post("/api/projects/{project_id}/smart-mode")
async def smart_mode(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    return await RadarService(active_provider(db), active_llm(db)).analyze_project(project_id)


@app.post("/api/projects/{project_id}/scan")
async def start_project_scan(project_id: int, full: bool = False, db: Session = Depends(get_db)):
    try:
        task_id = await RadarService(active_provider(db), active_llm(db)).start_scan(project_id, full)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"task_id": task_id, "status": "queued", "provider": active_provider(db).name}


@app.get("/api/projects/{project_id}/knowledge")
def list_knowledge(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    return db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.project_id == project_id).order_by(desc(KnowledgeEntry.updated_at))).all()


@app.post("/api/projects/{project_id}/knowledge")
def create_knowledge(project_id: int, payload: KnowledgeIn, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    entry = KnowledgeEntry(project_id=project_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.put("/api/knowledge/{knowledge_id}")
def update_knowledge(knowledge_id: int, payload: KnowledgeIn, db: Session = Depends(get_db)):
    entry = db.get(KnowledgeEntry, knowledge_id)
    if not entry:
        raise HTTPException(404, "知识库条目不存在")
    for key, value in payload.model_dump().items():
        setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/api/knowledge/{knowledge_id}")
def delete_knowledge(knowledge_id: int, db: Session = Depends(get_db)):
    entry = db.get(KnowledgeEntry, knowledge_id)
    if not entry:
        raise HTTPException(404, "知识库条目不存在")
    db.delete(entry)
    db.commit()
    return {"deleted": knowledge_id}


def _default_reply_policy(project_id: int) -> ReplyPolicy:
    settings = get_settings()
    return ReplyPolicy(project_id=project_id, auto_reply_enabled=settings.auto_reply_enabled, max_replies_per_hour=settings.auto_reply_max_per_hour, max_replies_per_day=settings.auto_reply_max_per_day, minimum_interval_seconds=settings.auto_reply_min_interval_seconds)


@app.get("/api/projects/{project_id}/reply-policy")
def get_reply_policy(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    policy = db.scalar(select(ReplyPolicy).where(ReplyPolicy.project_id == project_id))
    if policy is None:
        policy = _default_reply_policy(project_id)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


@app.put("/api/projects/{project_id}/reply-policy")
def put_reply_policy(project_id: int, payload: ReplyPolicyIn, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    policy = db.scalar(select(ReplyPolicy).where(ReplyPolicy.project_id == project_id))
    if policy is None:
        policy = ReplyPolicy(project_id=project_id)
        db.add(policy)
    for key, value in payload.model_dump().items():
        setattr(policy, key, value)
    db.commit()
    db.refresh(policy)
    return policy


@app.get("/api/douyin/accounts")
def list_douyin_accounts(db: Session = Depends(get_db)):
    return db.scalars(select(DouyinAccount).order_by(DouyinAccount.id)).all()


def _schedule_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _schedule_payload(schedule: ScanSchedule | None, project_id: int) -> dict:
    if schedule is None:
        return {"id": None, "project_id": project_id, "enabled": False, "interval_minutes": 30, "full": False, "next_run_at": None, "last_run_at": None}
    return {"id": schedule.id, "project_id": schedule.project_id, "enabled": schedule.enabled, "interval_minutes": schedule.interval_minutes, "full": schedule.full, "next_run_at": _schedule_iso(schedule.next_run_at), "last_run_at": _schedule_iso(schedule.last_run_at)}


@app.get("/api/projects/{project_id}/schedule", response_model=ScheduleOut)
def get_project_schedule(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    schedule = db.scalar(select(ScanSchedule).where(ScanSchedule.project_id == project_id))
    return _schedule_payload(schedule, project_id)


@app.put("/api/projects/{project_id}/schedule", response_model=ScheduleOut)
def put_project_schedule(project_id: int, payload: ScheduleIn, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    schedule = db.scalar(select(ScanSchedule).where(ScanSchedule.project_id == project_id))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if schedule is None:
        schedule = ScanSchedule(project_id=project_id)
        db.add(schedule)
        previous_enabled = False
        previous_interval = None
        previous_next_run_at = None
    else:
        previous_enabled = schedule.enabled
        previous_interval = schedule.interval_minutes
        previous_next_run_at = schedule.next_run_at
    schedule.enabled = payload.enabled
    schedule.interval_minutes = payload.interval_minutes
    schedule.full = payload.full
    if not payload.enabled:
        schedule.next_run_at = None
    elif payload.next_run_at is not None:
        requested = payload.next_run_at
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        schedule.next_run_at = requested.astimezone(timezone.utc).replace(tzinfo=None)
    elif not previous_next_run_at or not previous_enabled or previous_interval != payload.interval_minutes:
        schedule.next_run_at = now + timedelta(minutes=payload.interval_minutes)
    db.commit()
    db.refresh(schedule)
    return _schedule_payload(schedule, project_id)


@app.get("/api/projects/{project_id}/keywords")
def project_keywords(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    return db.scalars(select(Keyword).where(Keyword.project_id == project_id).order_by(desc(Keyword.opportunity_score))).all()


@app.get("/api/keywords/{keyword_id}")
def get_keyword(keyword_id: int, db: Session = Depends(get_db)):
    item = db.get(Keyword, keyword_id)
    if not item:
        raise HTTPException(404, "关键词不存在")
    return item


@app.patch("/api/keywords/{keyword_id}")
def update_keyword(keyword_id: int, enabled: bool, db: Session = Depends(get_db)):
    item = db.get(Keyword, keyword_id)
    if not item:
        raise HTTPException(404, "关键词不存在")
    item.enabled = enabled
    db.commit()
    return item


@app.get("/api/videos")
def list_videos(project_id: int | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = select(Video).order_by(desc(Video.opportunity_score)).limit(limit)
    if project_id is not None:
        query = query.where(Video.project_id == project_id)
    return db.scalars(query).all()


@app.get("/api/videos/{video_id}")
def get_video(video_id: int, db: Session = Depends(get_db)):
    item = db.get(Video, video_id)
    if not item:
        raise HTTPException(404, "视频不存在")
    return item


@app.post("/api/videos/{video_id}/scan")
async def scan_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "视频不存在")
    task_id = await RadarService(active_provider(db), active_llm(db)).start_scan(video.project_id)
    return {"task_id": task_id}


@app.get("/api/comments")
def list_comments(project_id: int | None = None, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    latest_reply_status = select(CommentReply.status).where(CommentReply.comment_id == Comment.id).order_by(desc(CommentReply.id)).limit(1).scalar_subquery()
    lead_alias = aliased(Lead)
    query = select(Comment, Video, lead_alias, latest_reply_status.label("reply_status")).join(Video, Comment.video_id == Video.id).outerjoin(LeadComment, LeadComment.comment_id == Comment.id).outerjoin(lead_alias, LeadComment.lead_id == lead_alias.id).order_by(desc(Comment.id)).limit(limit)
    if project_id is not None:
        query = query.where(Comment.project_id == project_id)
    rows = []
    for comment, video, lead, reply_status in db.execute(query).all():
        item = {column.name: getattr(comment, column.name) for column in Comment.__table__.columns}
        item.update({"video_title": video.title, "video_url": video.url, "lead_id": lead.id if lead else None, "lead_score": lead.lead_score if lead else None, "lead_level": lead.lead_level if lead else None, "intent_level": lead.intent_level if lead else None, "reply_status": reply_status})
        rows.append(item)
    return rows


def _comment_context(db: Session, comment: Comment) -> tuple[Project, Video, Lead | None, list[str]]:
    project = db.get(Project, comment.project_id)
    video = db.get(Video, comment.video_id)
    if not project or not video:
        raise HTTPException(409, "评论关联的项目或视频记录不存在")
    lead = db.scalar(select(Lead).join(LeadComment, LeadComment.lead_id == Lead.id).where(LeadComment.comment_id == comment.id))
    thread_ids = {value for value in (comment.platform_comment_id, comment.parent_comment_id) if value}
    thread_filter = [Comment.platform_comment_id.in_(thread_ids), Comment.parent_comment_id.in_(thread_ids)] if thread_ids else []
    history_filter = [Comment.platform_user_id == comment.platform_user_id] if comment.platform_user_id else []
    filters = [*history_filter, *thread_filter]
    history = db.scalars(select(Comment).where(Comment.project_id == project.id, Comment.platform == comment.platform, or_(*filters)).order_by(Comment.id)).all() if filters else [comment]
    if comment not in history:
        history.append(comment)
        history.sort(key=lambda row: row.id)
    return project, video, lead, [row.content for row in history]


def _agent_run_from_call(db: Session, project_id: int, agent: str, prompt_version: str, input_payload: dict, output: dict, llm: OpenAICompatibleProvider):
    call = llm.last_call
    input_text = call.input_text if call else json.dumps(input_payload, ensure_ascii=False, default=str)
    db.add(AgentRun(project_id=project_id, agent=agent, model=call.model if call else llm.model, prompt_version=prompt_version, input_hash=input_hash(input_payload), input_text=input_text, output=output, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=call.success if call else True, error=call.error if call else ""))


@app.get("/api/comments/{comment_id}")
def get_comment(comment_id: int, db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    _, video, lead, history = _comment_context(db, comment)
    return {"comment": comment, "video": video, "lead": lead, "history_text": history, "replies": db.scalars(select(CommentReply).where(CommentReply.comment_id == comment.id).order_by(desc(CommentReply.id))).all()}


@app.post("/api/comments/{comment_id}/analyze")
async def analyze_comment(comment_id: int, db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    project, video, lead, history = _comment_context(db, comment)
    llm = active_llm(db)
    project_data = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description, "keyword": video.keyword, "video_title": video.title, "video_description": video.description, "video_creator": video.creator, "video_likes": video.likes, "video_comments": video.comments, "video_shares": video.shares, "video_collects": video.collects, "history_text": "\n".join(f"{index + 1}. {text}" for index, text in enumerate(history))}
    comment_data = {"content": comment.content, "nickname": comment.nickname, "history_text": project_data["history_text"], "parent_comment_id": comment.parent_comment_id}
    llm.clear_last_call()
    try:
        judgment = await LeadJudgeAgent(llm).run(project_data, comment_data)
    except Exception:
        _agent_run_from_call(db, project.id, "LeadJudgeAgent", LeadJudgeAgent.prompt_version, {"project": project_data, "comment": comment_data}, {}, llm)
        db.commit()
        raise
    _agent_run_from_call(db, project.id, "LeadJudgeAgent", LeadJudgeAgent.prompt_version, {"project": project_data, "comment": comment_data}, judgment, llm)
    if judgment["is_lead"]:
        lead = _upsert_lead(db, project.id, comment, judgment, video.id)
    db.commit()
    return {"comment": comment, "judgment": judgment, "lead": lead}


def _reply_payload(db: Session, comment: Comment, decision: dict, *, reply_source: str = "AI") -> CommentReply:
    reply = CommentReply(project_id=comment.project_id, comment_id=comment.id, platform=comment.platform, reply_text=decision.get("reply_text", ""), reply_source=reply_source, status="WAITING_REVIEW" if decision.get("should_reply") and decision.get("reply_text") else "SKIPPED", generated_at=now_utc(), error_message=decision.get("reason", ""), error_code=",".join(decision.get("risk_flags", [])))
    db.add(reply)
    return reply


@app.post("/api/comments/{comment_id}/generate-reply")
async def generate_reply(comment_id: int, payload: GenerateReplyIn | None = None, db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    project, video, lead, history = _comment_context(db, comment)
    persona = db.scalar(select(Persona).where(Persona.project_id == project.id))
    knowledge = db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.project_id == project.id, KnowledgeEntry.enabled.is_(True))).all()
    previous = db.scalars(select(CommentReply).where(CommentReply.comment_id == comment.id).order_by(CommentReply.id)).all()
    llm = active_llm(db)
    agent = ReplyAgent(llm)
    comment_data = {"content": comment.content, "nickname": comment.nickname, "history_text": "\n".join(f"{index + 1}. {text}" for index, text in enumerate(history)), "video_title": video.title, "video_description": video.description}
    project_data = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description}
    lead_data = {column.name: getattr(lead, column.name) for column in Lead.__table__.columns} if lead else {}
    persona_data = {column.name: getattr(persona, column.name) for column in Persona.__table__.columns} if persona else {}
    llm.clear_last_call()
    try:
        decision = await agent.run(project_data, comment_data, lead_data, persona_data, [{"title": item.title, "content": item.content, "tags": item.tags, "enabled": item.enabled} for item in knowledge], [{"reply_text": item.reply_text, "status": item.status} for item in previous])
    except Exception:
        _agent_run_from_call(db, project.id, "ReplyAgent", agent.prompt_version, {"project": project_data, "comment": comment_data}, {}, llm)
        db.commit()
        raise
    decision_data = decision.model_dump()
    _agent_run_from_call(db, project.id, "ReplyAgent", agent.prompt_version, {"project": project_data, "comment": comment_data}, decision_data, llm)
    reply = _reply_payload(db, comment, decision_data)
    db.commit()
    db.refresh(reply)
    return {"decision": decision_data, "reply": reply}


@app.post("/api/comments/{comment_id}/reply")
async def send_comment_reply(comment_id: int, payload: ReplyActionIn, db: Session = Depends(get_db)):
    if not payload.confirm:
        raise HTTPException(400, "REPLY_CONFIRM_REQUIRED: 发送真实抖音回复必须明确 confirm=true")
    lock = _reply_send_locks.setdefault(comment_id, asyncio.Lock())
    async with lock:
        return await _send_comment_reply_locked(comment_id, payload, db)


@app.post("/api/comments/reply-batch")
async def send_comment_reply_batch(payload: ReplyBatchIn, db: Session = Depends(get_db)):
    """Send an explicitly confirmed batch serially through the same guards.

    Serial execution is intentional: one browser profile must not perform
    concurrent reply actions, and each item retains its own failure status.
    """
    if not payload.confirm:
        raise HTTPException(400, "REPLY_CONFIRM_REQUIRED: 批量发送真实抖音回复必须明确 confirm=true")

    results: list[dict] = []
    for item in payload.items:
        lock = _reply_send_locks.setdefault(item.comment_id, asyncio.Lock())
        try:
            async with lock:
                result = await _send_comment_reply_locked(
                    item.comment_id,
                    ReplyActionIn(reply_text=item.reply_text, confirm=True),
                    db,
                )
            results.append({"comment_id": item.comment_id, "ok": True, "result": result})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            results.append({"comment_id": item.comment_id, "ok": False, "status_code": exc.status_code, "error": detail})
        except DouyinError as exc:
            results.append({"comment_id": item.comment_id, "ok": False, "status_code": 409 if exc.code.startswith("DOUYIN_LOGIN") else 502, "error": {"code": exc.code, "message": exc.message, "detail": exc.detail}})
        except Exception as exc:
            results.append({"comment_id": item.comment_id, "ok": False, "status_code": 502, "error": {"code": "DOUYIN_REPLY_FAILED", "message": str(exc)}})
    successful = sum(1 for item in results if item["ok"])
    return {"ok": successful == len(results), "success_count": successful, "failed_count": len(results) - successful, "results": results}


async def _send_comment_reply_locked(comment_id: int, payload: ReplyActionIn, db: Session):
    # PostgreSQL serializes concurrent sends for the same comment. SQLite relies
    # on the process lock above; the conditional update below is still required
    # to prevent a second worker from claiming the same pending row.
    comment = db.scalar(select(Comment).where(Comment.id == comment_id).with_for_update())
    if not comment:
        raise HTTPException(404, "评论不存在")
    # Release only expired claims. A live SENDING row remains a hard block,
    # including across API workers, so a timeout can never silently become a
    # second real platform send.
    recover_stale_sending(db, comment_id=comment.id)
    project, video, lead, _ = _comment_context(db, comment)
    provider = active_provider(db)
    capabilities = getattr(provider, "capabilities", None)
    if not hasattr(provider, "reply_comment") or (capabilities is not None and not capabilities.get("reply_comment", False)):
        raise HTTPException(400, "当前 Provider 仅支持采集，不支持真实回复；请激活 Douyin Playwright")
    sent_reply = db.scalar(
        select(CommentReply)
        .where(
            CommentReply.comment_id == comment.id,
            CommentReply.status.in_(["SENDING", "SENT", "SENT_UNVERIFIED", "VERIFIED"]),
        )
        .order_by(desc(CommentReply.id))
    )
    if sent_reply is not None:
        code = "REPLY_SEND_IN_PROGRESS" if sent_reply.status == "SENDING" else "REPLY_ALREADY_SENT"
        message = "这条评论正在发送中，请勿重复提交" if sent_reply.status == "SENDING" else "这条评论已经有发送记录，系统已阻止重复回复"
        raise HTTPException(
            409,
            {
                "code": code,
                "message": message,
                "detail": {"reply_id": sent_reply.id, "status": sent_reply.status},
            },
        )
    enforce_send_policy(db, comment, lead=lead, automatic=False)
    reply = db.scalar(select(CommentReply).where(CommentReply.comment_id == comment.id, CommentReply.status.in_(["WAITING_REVIEW", "APPROVED", "DRAFT", "FAILED"])).order_by(desc(CommentReply.id)))
    if reply is None:
        reply = CommentReply(project_id=project.id, comment_id=comment.id, platform=comment.platform, reply_text=payload.reply_text, reply_source="MANUAL", status="APPROVED", approved_at=now_utc())
        db.add(reply)
    elif reply.status == "FAILED":
        raise HTTPException(409, {"code": "REPLY_RETRY_REQUIRES_REVIEW", "message": "发送失败的回复必须先执行 retry 审核转换", "detail": {"reply_id": reply.id}})
    elif payload.reply_text != reply.reply_text:
        reply.reply_text = payload.reply_text
        reply.reply_source = "MANUAL"
    reply.approved_at = reply.approved_at or now_utc()
    db.flush()
    claim = db.execute(
        update(CommentReply)
        .where(CommentReply.id == reply.id, CommentReply.status.in_(["WAITING_REVIEW", "APPROVED", "DRAFT"]))
        .values(
            status="SENDING",
            attempt_count=CommentReply.attempt_count + 1,
            approved_at=reply.approved_at,
            sending_started_at=now_utc(),
            send_lease_expires_at=now_utc() + timedelta(seconds=DEFAULT_SENDING_LEASE_SECONDS),
            error_code="",
            error_message="",
        )
    )
    if claim.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {"code": "REPLY_SEND_IN_PROGRESS", "message": "这条评论已被其他发送请求占用", "detail": {"reply_id": reply.id}})
    db.commit()
    db.refresh(reply)
    target = DouyinCommentDTO(platform=comment.platform, comment_id=comment.platform_comment_id, user_id=comment.platform_user_id, nickname=comment.nickname, profile_url=comment.profile_url, content=comment.content, created_at=comment.created_at_platform, parent_comment_id=comment.parent_comment_id, id_source=comment.id_source, comment_url=comment.comment_url)
    try:
        result = await provider.reply_comment(video.url, target, reply.reply_text)
    except DouyinError as exc:
        reply.status, reply.error_code, reply.error_message = "FAILED", exc.code, exc.message
        reply.sending_started_at = None
        reply.send_lease_expires_at = None
        db.commit()
        raise
    except Exception as exc:
        reply.status, reply.error_code, reply.error_message = "FAILED", "DOUYIN_REPLY_FAILED", str(exc)
        reply.sending_started_at = None
        reply.send_lease_expires_at = None
        db.commit()
        raise HTTPException(502, "DOUYIN_REPLY_FAILED: 真实回复执行失败") from exc
    reply.sent_at = now_utc()
    reply.sending_started_at = None
    reply.send_lease_expires_at = None
    reply.status = "VERIFIED" if result.status is ReplyStatus.VERIFIED else "SENT_UNVERIFIED"
    if result.status is ReplyStatus.VERIFIED:
        reply.verified_at = now_utc()
    else:
        reply.verification_due_at = now_utc() + timedelta(minutes=15)
    db.commit()
    db.refresh(reply)
    return {"reply": reply, "provider_result": result}


@app.get("/api/replies")
def list_replies(project_id: int | None = None, status: str | None = None, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    query = select(CommentReply).order_by(desc(CommentReply.id)).limit(limit)
    if project_id is not None:
        query = query.where(CommentReply.project_id == project_id)
    if status:
        query = query.where(CommentReply.status == status)
    return db.scalars(query).all()


@app.patch("/api/replies/{reply_id}")
def review_reply(reply_id: int, payload: ReplyReviewIn, db: Session = Depends(get_db)):
    """Apply an explicit review transition without contacting Douyin."""
    reply = db.get(CommentReply, reply_id)
    if not reply:
        raise HTTPException(404, "回复记录不存在")

    if payload.action == "approve":
        if reply.status not in {"DRAFT", "WAITING_REVIEW", "FAILED"}:
            raise HTTPException(409, {"code": "REPLY_INVALID_TRANSITION", "message": f"状态 {reply.status} 不能批准", "detail": {"reply_id": reply.id}})
        text_value = (payload.reply_text if payload.reply_text is not None else reply.reply_text).strip()
        if not text_value:
            raise HTTPException(422, {"code": "REPLY_TEXT_REQUIRED", "message": "批准前必须提供回复文本", "detail": {"reply_id": reply.id}})
        reply.reply_text = text_value
        reply.status = "APPROVED"
        reply.approved_at = now_utc()
        reply.error_code = ""
        reply.error_message = ""
    elif payload.action == "skip":
        if reply.status not in {"DRAFT", "WAITING_REVIEW", "APPROVED", "FAILED"}:
            raise HTTPException(409, {"code": "REPLY_INVALID_TRANSITION", "message": f"状态 {reply.status} 不能跳过", "detail": {"reply_id": reply.id}})
        reply.status = "SKIPPED"
        reply.error_code = "MANUAL_SKIPPED"
        reply.error_message = "人工审核跳过"
    else:
        if reply.status != "FAILED":
            raise HTTPException(409, {"code": "REPLY_INVALID_TRANSITION", "message": "只有发送失败的回复可以重试", "detail": {"reply_id": reply.id, "status": reply.status}})
        reply.status = "WAITING_REVIEW"
        reply.approved_at = None
        reply.error_code = ""
        reply.error_message = ""

    db.commit()
    db.refresh(reply)
    return reply


@app.post("/api/replies/{reply_id}/verify")
async def verify_reply(reply_id: int, db: Session = Depends(get_db)):
    """Reconcile a sent reply by reading the real Douyin DOM again.

    Verification is read-only from the platform perspective: it never retries
    the send action.  Keeping this endpoint explicit prevents a lost response
    after a real click from turning into a duplicate reply on retry.
    """

    reply = db.get(CommentReply, reply_id)
    if not reply:
        raise HTTPException(404, "回复记录不存在")
    if reply.status not in {"SENT_UNVERIFIED", "SENT"}:
        raise HTTPException(
            409,
            {
                "code": "REPLY_NOT_PENDING_VERIFICATION",
                "message": f"状态 {reply.status} 不需要重新核验",
                "detail": {"reply_id": reply.id, "status": reply.status},
            },
        )
    comment = db.get(Comment, reply.comment_id)
    if not comment:
        raise HTTPException(409, "回复关联的评论记录不存在")
    video = db.get(Video, comment.video_id)
    if not video:
        raise HTTPException(409, "回复关联的视频记录不存在")
    provider = active_provider(db)
    if not hasattr(provider, "verify_reply"):
        raise HTTPException(400, "当前 Provider 不支持真实回复核验")
    target = DouyinCommentDTO(
        platform=comment.platform,
        comment_id=comment.platform_comment_id,
        user_id=comment.platform_user_id,
        nickname=comment.nickname,
        profile_url=comment.profile_url,
        content=comment.content,
        created_at=comment.created_at_platform,
        parent_comment_id=comment.parent_comment_id,
        id_source=comment.id_source,
        comment_url=comment.comment_url,
    )
    try:
        result = await provider.verify_reply(video.url, target, reply.reply_text)
    except DouyinError as exc:
        reply.error_code = exc.code
        reply.error_message = exc.message
        db.commit()
        raise
    except Exception as exc:
        reply.error_code = "DOUYIN_REPLY_VERIFY_FAILED"
        reply.error_message = str(exc)
        db.commit()
        raise HTTPException(502, "DOUYIN_REPLY_VERIFY_FAILED: 真实回复核验失败") from exc
    record_reply_verification(
        db,
        reply.id,
        verified=result.status is ReplyStatus.VERIFIED,
        platform_reply_id=(result.detail or {}).get("platform_reply_id"),
        error_code="REPLY_NOT_VERIFIED" if result.status is not ReplyStatus.VERIFIED else "",
        error_message="已重新读取页面，但尚未观察到精确回复文本" if result.status is not ReplyStatus.VERIFIED else "",
    )
    if result.status is ReplyStatus.VERIFIED:
        reply.error_code = ""
        reply.error_message = ""
    else:
        reply.verification_due_at = now_utc() + timedelta(minutes=15)
        reply.error_code = "REPLY_NOT_VERIFIED"
        reply.error_message = "已重新读取页面，但尚未观察到精确回复文本"
    db.commit()
    db.refresh(reply)
    return {"reply": reply, "provider_result": result}


def lead_payload(db: Session, lead: Lead):
    comments = db.scalars(select(Comment).join(LeadComment, LeadComment.comment_id == Comment.id).where(LeadComment.lead_id == lead.id).order_by(Comment.id)).all()
    videos = db.scalars(select(Video).join(LeadSource, LeadSource.video_id == Video.id).where(LeadSource.lead_id == lead.id)).all()
    events = db.scalars(select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.created_at)).all()
    return {**{column.name: getattr(lead, column.name) for column in Lead.__table__.columns}, "comments": comments, "videos": videos, "score_history": events}


@app.get("/api/leads")
def list_leads(project_id: int | None = None, level: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    query = select(Lead).order_by(desc(Lead.lead_score))
    if project_id:
        query = query.where(Lead.project_id == project_id)
    if level:
        query = query.where(Lead.lead_level == level)
    if status:
        query = query.where(Lead.status == status)
    return db.scalars(query.limit(200)).all()


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "潜客不存在")
    return lead_payload(db, lead)


@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: int, payload: LeadStatusUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "潜客不存在")
    previous = lead.status
    lead.status = payload.status
    if previous != payload.status:
        db.add(LeadEvent(lead_id=lead.id, score=lead.lead_score, event_type="status_changed", note=f"{previous} -> {payload.status}"))
    db.commit()
    db.refresh(lead)
    return lead


@app.post("/api/leads/{lead_id}/persona")
async def lead_persona(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "潜客不存在")
    project = db.get(Project, lead.project_id)
    persona = db.scalar(select(Persona).where(Persona.project_id == project.id))
    if not persona:
        persona = Persona(project_id=project.id, name="行业顾问", identity="本地行业顾问")
        db.add(persona)
        db.commit()
    comments = db.scalars(select(Comment).join(LeadComment, LeadComment.comment_id == Comment.id).where(LeadComment.lead_id == lead.id).order_by(Comment.id)).all()
    project_data = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description}
    lead_data = {"need": lead.need, "budget": lead.budget, "summary": lead.summary, "location": lead.location, "purchase_stage": lead.purchase_stage}
    persona_data = {"name": persona.name, "identity": persona.identity, "experience": persona.experience, "location": persona.location, "tone": persona.tone, "strengths": persona.strengths, "forbidden_words": persona.forbidden_words, "sample_reply": persona.sample_reply}
    llm = active_llm(db)
    agent = PersonaAgent(llm)
    persona_input = {"project": project_data, "lead": lead_data, "persona": persona_data, "comments": [comment.content for comment in comments]}
    input_text = json.dumps(persona_input, ensure_ascii=False)
    llm.clear_last_call()
    try:
        advice = await agent.run(project_data, lead_data, persona_data, persona_input["comments"])
    except Exception as exc:
        call = llm.last_call
        failed_input_text = call.input_text if call and call.input_text else input_text
        failed_model = call.model if call else llm.model
        failed_run = AgentRun(project_id=project.id, agent="PersonaAgent", model=failed_model, prompt_version=agent.prompt_version, input_hash=input_hash(persona_input), input_text=failed_input_text, output={}, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=False, error=str(exc))
        db.add(failed_run)
        db.commit()
        raise
    call = llm.last_call
    model = call.model if call else llm.model
    if call and call.input_text:
        input_text = call.input_text
    agent_run = AgentRun(project_id=project.id, agent="PersonaAgent", model=model, prompt_version=agent.prompt_version, input_hash=input_hash(persona_input), input_text=input_text, output=advice, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=call.success if call else True, error=call.error if call else "")
    agent_run.input_text = input_text
    db.add(agent_run)
    lead.persona_advice = advice
    db.commit()
    return advice


@app.post("/api/projects/{project_id}/personas")
def save_persona(project_id: int, payload: PersonaIn, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    persona = db.scalar(select(Persona).where(Persona.project_id == project_id))
    if persona:
        for key, value in payload.model_dump().items():
            setattr(persona, key, value)
    else:
        persona = Persona(project_id=project_id, **payload.model_dump())
        db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


@app.get("/api/projects/{project_id}/personas")
def get_persona(project_id: int, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    persona = db.scalar(select(Persona).where(Persona.project_id == project_id))
    if persona is not None:
        return persona
    return {
        "id": None,
        "project_id": project_id,
        "name": "行业顾问",
        "identity": "本地行业顾问",
        "experience": "",
        "location": "",
        "tone": "专业但不推销",
        "strengths": "",
        "forbidden_words": "",
        "sample_reply": "",
    }


@app.get("/api/tasks")
def list_tasks(project_id: int | None = None, db: Session = Depends(get_db)):
    query = select(ScanTask).order_by(desc(ScanTask.created_at)).limit(50)
    if project_id is not None:
        query = query.where(ScanTask.project_id == project_id)
    return db.scalars(query).all()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ScanTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"task": task, "steps": db.scalars(select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.id)).all(), "events": db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)).all(), "checkpoint": db.get(TaskCheckpoint, task_id), "report": db.scalar(select(TaskReport).where(TaskReport.task_id == task_id))}


def mutate_task(task_id: int, status: str, db: Session):
    task = db.get(ScanTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    transitions = {
        "pause": {"queued": "paused", "running": "paused"},
        "resume": {"paused": "queued"},
        "retry": {"failed": "queued"},
    }
    action = status
    next_status = transitions.get(action, {}).get(task.status)
    if next_status is None:
        raise HTTPException(
            409,
            {
                "code": "TASK_INVALID_TRANSITION",
                "message": f"任务状态 {task.status} 不能执行 {action}",
                "detail": {"task_id": task.id, "status": task.status, "action": action},
            },
        )
    task.status = next_status
    db.commit()
    return task


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: int, db: Session = Depends(get_db)):
    return mutate_task(task_id, "pause", db)


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: int, db: Session = Depends(get_db)):
    task = mutate_task(task_id, "resume", db)
    return task


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    task = mutate_task(task_id, "retry", db)
    task.error = ""
    # Retry resumes from the last durable checkpoint.  Clearing it would
    # re-scan already judged comments and could create duplicate work.
    task.finished_at = None
    for step in db.scalars(select(TaskStep).where(TaskStep.task_id == task_id)).all():
        if step.status != "completed":
            step.status, step.detail, step.started_at, step.finished_at = "queued", "", None, None
    db.commit()
    return task


@app.get("/api/providers")
def list_providers(db: Session = Depends(get_db)):
    return db.scalars(select(ProviderRecord).order_by(ProviderRecord.id)).all()


@app.post("/api/providers/{provider_id}/health")
async def provider_health(provider_id: int, db: Session = Depends(get_db)):
    record = db.get(ProviderRecord, provider_id)
    if not record:
        raise HTTPException(404, "Provider 不存在")
    provider = next((item for item in provider_registry() if item.name == record.name), None)
    if provider is None:
        raise HTTPException(400, "Provider 未在当前配置中注册")
    result = await provider.health_check()
    record.status, record.note, record.checked_at = result.status, result.message, now_utc()
    db.commit()
    return record


@app.post("/api/providers/{provider_id}/activate")
def activate_provider(provider_id: int, db: Session = Depends(get_db)):
    record = db.get(ProviderRecord, provider_id)
    if not record:
        raise HTTPException(404, "Provider 不存在")
    known = {provider.name for provider in provider_registry()}
    if record.name not in known:
        raise HTTPException(400, "Provider 未在当前配置中注册")
    setting = db.get(Setting, "content_provider")
    if setting:
        setting.value = record.name
    else:
        db.add(Setting(key="content_provider", value=record.name))
    db.commit()
    return {"active": record.name, "provider": record}


@app.get("/api/dashboard")
def dashboard(project_id: int | None = None, db: Session = Depends(get_db)):
    project = db.get(Project, project_id) if project_id else db.scalar(select(Project).order_by(Project.id))
    if not project:
        return {"project": None, "stats": {}, "events": []}
    stats = {"keywords": db.scalar(select(func.count(Keyword.id)).where(Keyword.project_id == project.id)) or 0, "videos": db.scalar(select(func.count(Video.id)).where(Video.project_id == project.id)) or 0, "comments": db.scalar(select(func.count(Comment.id)).where(Comment.project_id == project.id)) or 0, "leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id)) or 0, "s_leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.lead_level == "S")) or 0, "new_leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.status == "NEW")) or 0}
    events = db.scalars(select(TaskEvent).where(TaskEvent.project_id == project.id).order_by(desc(TaskEvent.id)).limit(20)).all()
    top_keywords = db.scalars(select(Keyword).where(Keyword.project_id == project.id).order_by(desc(Keyword.opportunity_score)).limit(8)).all()
    done = sum([bool(project.intelligence), stats["keywords"] > 0, stats["videos"] > 0, stats["leads"] > 0])
    provider = active_provider(db)
    mode = "抖音 DOM + 文本模型"
    return {"project": project, "stats": stats, "events": list(reversed(events)), "top_keywords": top_keywords, "mode": mode, "checklist": {"done": done, "total": 6}}


@app.get("/api/analytics")
def analytics(project_id: int | None = None, db: Session = Depends(get_db)):
    project = db.get(Project, project_id) if project_id else db.scalar(select(Project).order_by(Project.id))
    if not project:
        return {}
    levels = {level: db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.lead_level == level)) or 0 for level in ["S", "A", "B", "C"]}
    categories = db.execute(select(Keyword.category, func.count(Keyword.id)).where(Keyword.project_id == project.id).group_by(Keyword.category)).all()
    keyword_total = db.scalar(select(func.count(Keyword.id)).where(Keyword.project_id == project.id)) or 0
    keyword_scanned = db.scalar(select(func.count(Keyword.id)).where(Keyword.project_id == project.id, Keyword.last_scanned_at.is_not(None))) or 0
    comment_total = db.scalar(select(func.count(Comment.id)).where(Comment.project_id == project.id)) or 0
    comment_coverage_status = _aggregate_coverage_status(
        set(db.scalars(select(Comment.coverage_status).where(Comment.project_id == project.id)).all())
    )
    judge_total = db.scalar(select(func.count(AgentRun.id)).where(AgentRun.project_id == project.id, AgentRun.agent == "LeadJudgeAgent")) or 0
    judge_success = db.scalar(select(func.count(AgentRun.id)).where(AgentRun.project_id == project.id, AgentRun.agent == "LeadJudgeAgent", AgentRun.success.is_(True))) or 0
    comment_coverage_score = {"complete": 100, "partial": 50, "unknown": 0}[comment_coverage_status]
    health = {
        "keyword_coverage": round(keyword_scanned / keyword_total * 100) if keyword_total else 0,
        "comment_coverage": comment_coverage_score if comment_total else 0,
        "comment_coverage_status": comment_coverage_status,
        "judgement_success_rate": round(judge_success / judge_total * 100) if judge_total else 0,
    }
    health["overall"] = round((health["keyword_coverage"] + health["comment_coverage"] + health["judgement_success_rate"]) / 3)
    next_step = "先创建项目并运行智能模式。" if not project.intelligence else "继续扫描并人工复核高价值潜客。"
    return {"levels": levels, "categories": [{"name": name, "value": count} for name, count in categories], "health": health, "next_step": next_step, "conversion_note": "成交结果需要人工回填，系统不自动发送消息。"}


@app.get("/api/settings")
def get_settings_api(db: Session = Depends(get_db)):
    settings = get_settings()
    stored = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    resolved = settings_with_db(settings, stored)
    return {"llm_base_url": resolved.llm_base_url, "llm_api_key": "", "llm_api_key_configured": bool(resolved.llm_api_key), "llm_model": resolved.llm_model, "llm_temperature": resolved.llm_temperature, "llm_timeout": resolved.llm_timeout, "content_provider": stored.get("content_provider", settings.content_provider), "policy": "text-only"}


@app.put("/api/settings")
def update_settings(payload: SettingsInput, db: Session = Depends(get_db)):
    allowed = {"llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout"}
    values = payload.model_dump(exclude_none=True)
    for key, value in values.items():
        if key not in allowed:
            continue
        if key == "llm_api_key" and not value:
            continue
        if key == "llm_api_key":
            try:
                value = encrypt_secret(value, get_settings())
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
        if key in {"llm_temperature", "llm_timeout"}:
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, f"{key} 必须是数字") from exc
            if key == "llm_temperature" and not 0 <= numeric <= 2:
                raise HTTPException(422, "llm_temperature 必须在 0 到 2 之间")
            if key == "llm_timeout" and not 1 <= numeric <= 180:
                raise HTTPException(422, "llm_timeout 必须在 1 到 180 秒之间")
            value = str(numeric)
        setting = db.get(Setting, key)
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()
    return get_settings_api(db)


@app.post("/api/settings/test-llm")
async def test_llm(payload: SettingsInput | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    stored = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    for key, value in (payload.model_dump(exclude_none=True) if payload else {}).items():
        if key in {"llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout"} and value:
            stored[key] = value
    return await OpenAICompatibleProvider(settings_with_db(get_settings(), stored)).test_connection()


@app.get("/api/agent-runs")
def list_agent_runs(project_id: int | None = None, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    query = select(AgentRun).order_by(desc(AgentRun.id)).limit(limit)
    if project_id is not None:
        query = query.where(AgentRun.project_id == project_id)
    return db.scalars(query).all()


def _resolve_sse_cursor(request: Request, query_cursor: int) -> int:
    header_value = request.headers.get("last-event-id")
    if not header_value:
        return query_cursor
    try:
        return max(query_cursor, int(header_value.strip()))
    except ValueError:
        return query_cursor


@app.get("/api/events/stream")
async def events_stream(request: Request, last_event_id: int = 0, project_id: int | None = None):
    last_event_id = _resolve_sse_cursor(request, last_event_id)
    queue = event_bus.subscribe()

    async def generator():
        try:
            yield ": connected\n\n"
            with SessionLocal() as db:
                query = select(TaskEvent).where(TaskEvent.id > last_event_id).order_by(TaskEvent.id)
                if project_id:
                    query = query.where(TaskEvent.project_id == project_id)
                for event in db.scalars(query).all():
                    yield sse_line({"id": event.id, "project_id": event.project_id, "event_type": event.event_type, "message": event.message, "payload": event.payload, "created_at": event.created_at.isoformat()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if project_id and event.get("project_id") != project_id:
                        continue
                    yield sse_line(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
