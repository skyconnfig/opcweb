import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.agents.persona_agent import PersonaAgent
from app.core.config import get_settings
from app.db import SessionLocal, get_db
from app.models import AgentRun, Comment, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Persona, Project, ProviderRecord, ScanTask, Setting, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, Video, now_utc
from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.external.mediacrawler import MediaCrawlerExternalProvider
from app.providers.external.social_harvest import SocialHarvestExternalProvider
from app.providers.mock.mock_provider import MockProvider
from app.seed import init_database
from app.services.event_bus import event_bus, sse_line
from app.services.radar_service import RadarService


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


class PersonaIn(BaseModel):
    name: str
    identity: str = ""
    experience: str = ""
    location: str = ""
    tone: str = "专业但不推销"
    strengths: str = ""
    forbidden_words: str = ""
    sample_reply: str = ""


def provider_registry():
    settings = get_settings()
    return [
        MockProvider(),
        DouyinCommentsCrawlerExternalProvider(settings.douyin_comments_crawler_url),
        MediaCrawlerExternalProvider(settings.mediacrawler_path),
        SocialHarvestExternalProvider(settings.social_harvest_path),
    ]


def active_provider():
    return MockProvider()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    with SessionLocal() as db:
        for provider in provider_registry():
            record = db.scalar(select(ProviderRecord).where(ProviderRecord.name == provider.name))
            if not record:
                db.add(ProviderRecord(name=provider.name, kind="mock" if provider.name == "Mock Provider" else "external", status="connected" if provider.name == "Mock Provider" else "disconnected", platform=provider.platform, capabilities=provider.capabilities, endpoint=getattr(provider, "base_url", ""), note="Demo 数据源" if provider.name == "Mock Provider" else "需用户独立启动/配置"))
        db.commit()
    yield


app = FastAPI(title="AI 截流雷达", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "running", "service": "AI 截流雷达", "version": "0.1.0", "mode": "mock-demo-fallback"}


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
    return await RadarService(active_provider()).analyze_project(project_id)


@app.post("/api/projects/{project_id}/scan")
async def start_project_scan(project_id: int, full: bool = False, db: Session = Depends(get_db)):
    try:
        task_id = await RadarService(active_provider()).start_scan(project_id, full)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"task_id": task_id, "status": "queued", "provider": active_provider().name}


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
    task_id = await RadarService(active_provider()).start_scan(video.project_id)
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
    advice = await PersonaAgent().run({"industry": project.industry, "location": project.location, "service": project.service}, {"need": lead.need, "budget": lead.budget, "summary": lead.summary}, {"name": persona.name})
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
    asyncio.create_task(RadarService(active_provider()).run_task(task_id))
    return task


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    task = mutate_task(task_id, "queued", db)
    task.error = ""
    db.commit()
    asyncio.create_task(RadarService(active_provider()).run_task(task_id))
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
    return {item.key: item.value for item in db.scalars(select(Setting)).all()}


@app.put("/api/settings")
def update_settings(values: dict[str, str], db: Session = Depends(get_db)):
    for key, value in values.items():
        setting = db.get(Setting, key)
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()
    return {item.key: item.value for item in db.scalars(select(Setting)).all()}


@app.get("/api/events/stream")
async def events_stream():
    queue = event_bus.subscribe()

    async def generator():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    yield sse_line(await asyncio.wait_for(queue.get(), timeout=15))
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
