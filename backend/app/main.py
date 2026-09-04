import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from app.agents.llm import OpenAICompatibleProvider, input_hash, settings_with_db
from app.agents.persona_agent import PersonaAgent
from app.core.config import get_settings
from app.db import SessionLocal, get_db
from app.models import AgentRun, Comment, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Persona, Project, ProviderRecord, ScanSchedule, ScanTask, Setting, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, Video, now_utc
from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.external.mediacrawler import MediaCrawlerExternalProvider
from app.providers.external.social_harvest import SocialHarvestExternalProvider
from app.providers.mock.mock_provider import MockProvider
from app.seed import init_database
from app.security import auth_middleware
from app.settings_store import encrypt_secret, read_setting
from app.services.event_bus import event_bus, sse_line
from app.services.radar_service import RadarService
from app.tasks.scheduler import create_scheduler, enqueue_due_schedules


class ProjectCreate(BaseModel):
    name: str = "我的行业雷达"
    industry: str
    location: str = ""
    service: str = ""
    price_range: str = ""
    target_customer: str = ""
    description: str = ""


class ProjectOut(ProjectCreate):
    id: int
    status: str
    intelligence: dict = {}
    model_config = ConfigDict(from_attributes=True)


class ScheduleIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=180, ge=15, le=10080)
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


def provider_registry(settings=None):
    settings = settings or get_settings()
    return [
        MockProvider(),
        DouyinCommentsCrawlerExternalProvider(settings.douyin_comments_crawler_url),
        MediaCrawlerExternalProvider(settings.mediacrawler_path),
        SocialHarvestExternalProvider(settings.social_harvest_path),
    ]


def active_provider(db: Session | None = None):
    settings = get_settings()
    selected = settings.content_provider
    if db:
        stored = db.get(Setting, "content_provider")
        selected = stored.value if stored else selected
    aliases = {
        "mock": "Mock Provider",
        "douyin-comments-crawler": "Douyin Comments Crawler",
        "mediacrawler": "MediaCrawler (external)",
        "social-harvest": "Social Harvest (external)",
    }
    selected_name = aliases.get(selected, selected)
    return next((provider for provider in provider_registry(settings) if provider.name == selected_name), MockProvider())


def active_llm(db: Session) -> OpenAICompatibleProvider:
    settings = get_settings()
    values = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    return OpenAICompatibleProvider(settings_with_db(settings, values))


async def _task_worker(stop: asyncio.Event):
    while not stop.is_set():
        task_id = None
        full = False
        with SessionLocal() as db:
            task = db.scalar(select(ScanTask).where(ScanTask.status == "queued").order_by(ScanTask.created_at).limit(1))
            if task:
                task.status = "running"
                db.commit()
                task_id, full = task.id, task.full
                provider = active_provider(db)
                llm = active_llm(db)
        if task_id is not None:
            await RadarService(provider, llm).run_task(task_id, full=full)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    with SessionLocal() as db:
        db.execute(update(ScanTask).where(ScanTask.status == "running").values(status="queued", error=""))
        for provider in provider_registry():
            record = db.scalar(select(ProviderRecord).where(ProviderRecord.name == provider.name))
            if not record:
                db.add(ProviderRecord(name=provider.name, kind="mock" if provider.name == "Mock Provider" else "external", status="connected" if provider.name == "Mock Provider" else "disconnected", platform=provider.platform, capabilities=provider.capabilities, endpoint=getattr(provider, "base_url", ""), note="Demo 数据源" if provider.name == "Mock Provider" else "需用户独立启动/配置"))
        db.commit()
    scheduler = create_scheduler()
    scheduler.add_job(
        enqueue_due_schedules,
        "interval",
        minutes=1,
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


app = FastAPI(title="AI 截流雷达", version="0.1.0", lifespan=lifespan)
app.middleware("http")(auth_middleware)
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health(db: Session = Depends(get_db)):
    provider = active_provider(db)
    return {"status": "running", "service": "AI 截流雷达", "version": "0.1.0", "mode": "mock-demo-fallback" if provider.name == "Mock Provider" else "text-production", "provider": provider.name}


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


def _schedule_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _schedule_payload(schedule: ScanSchedule | None, project_id: int) -> dict:
    if schedule is None:
        return {"id": None, "project_id": project_id, "enabled": False, "interval_minutes": 180, "full": False, "next_run_at": None, "last_run_at": None}
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
    elif not schedule.next_run_at or not schedule.enabled:
        schedule.next_run_at = now + timedelta(minutes=payload.interval_minutes)
    db.commit()
    db.refresh(schedule)
    return _schedule_payload(schedule, project_id)


@app.get("/api/projects/{project_id}/keywords")
def project_keywords(project_id: int, db: Session = Depends(get_db)):
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
def list_videos(project_id: int | None = None, limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    query = select(Video).order_by(desc(Video.opportunity_score)).limit(limit)
    if project_id:
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
def list_comments(project_id: int | None = None, limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    query = select(Comment).order_by(desc(Comment.id)).limit(limit)
    if project_id:
        query = query.where(Comment.project_id == project_id)
    return db.scalars(query).all()


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
        failed_model = call.model if call else (llm.model if llm.configured else "deterministic-mock")
        failed_run = AgentRun(project_id=project.id, agent="PersonaAgent", model=failed_model, prompt_version=agent.prompt_version, input_hash=input_hash(persona_input), input_text=failed_input_text, output={}, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=False, error=str(exc))
        db.add(failed_run)
        db.commit()
        raise HTTPException(502, "文本模型调用失败") from exc
    call = llm.last_call
    model = call.model if call else (llm.model if llm.configured else "deterministic-mock")
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


@app.get("/api/tasks")
def list_tasks(project_id: int | None = None, db: Session = Depends(get_db)):
    query = select(ScanTask).order_by(desc(ScanTask.created_at)).limit(50)
    if project_id:
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
    task.status = status
    db.commit()
    return task


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: int, db: Session = Depends(get_db)):
    return mutate_task(task_id, "paused", db)


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: int, db: Session = Depends(get_db)):
    task = mutate_task(task_id, "queued", db)
    return task


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    task = mutate_task(task_id, "queued", db)
    task.error = ""
    checkpoint = db.get(TaskCheckpoint, task_id)
    if checkpoint:
        checkpoint.last_keyword_id = 0
        checkpoint.last_video_id = 0
        checkpoint.last_comment_cursor = ""
        checkpoint.processed_comment_ids = []
    for step in db.scalars(select(TaskStep).where(TaskStep.task_id == task_id)).all():
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
    provider = next(item for item in provider_registry() if item.name == record.name)
    result = await provider.health_check()
    record.status, record.note, record.checked_at = result.status, result.message, now_utc()
    db.commit()
    return record


@app.post("/api/providers/{provider_id}/activate")
def activate_provider(provider_id: int, db: Session = Depends(get_db)):
    record = db.get(ProviderRecord, provider_id)
    if not record:
        raise HTTPException(404, "Provider 不存在")
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
    return {"project": project, "stats": stats, "events": list(reversed(events)), "mode": "Demo 数据模式"}


@app.get("/api/analytics")
def analytics(project_id: int | None = None, db: Session = Depends(get_db)):
    project = db.get(Project, project_id) if project_id else db.scalar(select(Project).order_by(Project.id))
    if not project:
        return {}
    levels = {level: db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.lead_level == level)) or 0 for level in ["S", "A", "B", "C"]}
    categories = db.execute(select(Keyword.category, func.count(Keyword.id)).where(Keyword.project_id == project.id).group_by(Keyword.category)).all()
    return {"levels": levels, "categories": [{"name": name, "value": count} for name, count in categories], "conversion_note": "成交结果需要人工回填，系统不自动发送消息。"}


@app.get("/api/settings")
def get_settings_api(db: Session = Depends(get_db)):
    settings = get_settings()
    stored = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    resolved = settings_with_db(settings, stored)
    return {"llm_base_url": resolved.llm_base_url, "llm_api_key": "", "llm_api_key_configured": bool(resolved.llm_api_key), "llm_model": resolved.llm_model, "llm_temperature": resolved.llm_temperature, "llm_timeout": resolved.llm_timeout, "content_provider": stored.get("content_provider", settings.content_provider), "policy": "text-only"}


@app.put("/api/settings")
def update_settings(values: dict[str, str], db: Session = Depends(get_db)):
    allowed = {"llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout"}
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
async def test_llm(values: dict[str, str] | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    stored = {item.key: read_setting(item.key, item.value, settings) for item in db.scalars(select(Setting)).all()}
    for key, value in (values or {}).items():
        if key in {"llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout"} and value:
            stored[key] = value
    return await OpenAICompatibleProvider(settings_with_db(get_settings(), stored)).test_connection()


@app.get("/api/agent-runs")
def list_agent_runs(project_id: int | None = None, limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    query = select(AgentRun).order_by(desc(AgentRun.id)).limit(limit)
    if project_id:
        query = query.where(AgentRun.project_id == project_id)
    return db.scalars(query).all()


@app.get("/api/events/stream")
async def events_stream(last_event_id: int = 0, project_id: int | None = None):
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
