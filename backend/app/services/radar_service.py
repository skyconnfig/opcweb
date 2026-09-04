import asyncio
import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.agents.industry_agent import IndustryAgent
from app.agents.keyword_agent import KeywordAgent, keyword_opportunity_score
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.persona_agent import PersonaAgent
from app.db import SessionLocal
from app.models import AgentRun, Comment, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Project, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, ScanTask, Video, now_utc
from app.providers.base import BaseContentProvider
from app.services.event_bus import event_bus


def fingerprint(content: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", content).lower().encode("utf-8")).hexdigest()


def lead_level(score: float) -> str:
    return "S" if score >= 90 else "A" if score >= 75 else "B" if score >= 60 else "C"


class RadarService:
    def __init__(self, provider: BaseContentProvider):
        self.provider = provider
        self.industry_agent = IndustryAgent()
        self.keyword_agent = KeywordAgent()
        self.judge_agent = LeadJudgeAgent()
        self.persona_agent = PersonaAgent()
        self.prefilter = RulePreFilter()

    async def analyze_project(self, project_id: int) -> dict:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if not project:
                raise ValueError("项目不存在")
            context = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description}
            intelligence = await self.industry_agent.run(context)
            project.intelligence = intelligence
            db.add(AgentRun(project_id=project.id, agent="IndustryAgent", prompt_version=self.industry_agent.prompt_version, input_hash=fingerprint(str(context)), output=intelligence))
            keywords = await self.keyword_agent.run(context, intelligence)
            for row in keywords:
                db.add(Keyword(project_id=project.id, **row))
            project.status = "ready"
            db.commit()
            return {"intelligence": intelligence, "keyword_count": len(keywords), "categories": _category_counts(keywords)}

    async def start_scan(self, project_id: int, full: bool = False) -> int:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if not project:
                raise ValueError("项目不存在")
            task = ScanTask(project_id=project_id, name=f"{project.location}{project.industry}行业扫描", status="queued")
            db.add(task)
            db.flush()
            for name in ["generate_keywords", "schedule_keywords", "scan_keyword", "discover_videos", "rank_videos", "scan_comments", "prefilter_comments", "judge_leads", "deduplicate_leads", "update_dashboard"]:
                db.add(TaskStep(task_id=task.id, name=name))
            db.add(TaskCheckpoint(task_id=task.id))
            db.commit()
            task_id = task.id
        asyncio.create_task(self.run_task(task_id, full=full))
        return task_id

    async def run_task(self, task_id: int, full: bool = False):
        with SessionLocal() as db:
            task = db.get(ScanTask, task_id)
            if not task:
                return
            task.status, task.started_at = "running", now_utc()
            project = db.get(Project, task.project_id)
            db.commit()
        await self.emit(task_id, project.id, "task.started", f"开始扫描：{project.location}{project.industry}")
        try:
            await self._step(task_id, "generate_keywords", "正在理解行业与客户语言", 0.18)
            with SessionLocal() as db:
                keywords = db.scalars(select(Keyword).where(Keyword.project_id == project.id, Keyword.enabled.is_(True)).order_by(Keyword.opportunity_score.desc())).all()
            if not keywords:
                await self.analyze_project(project.id)
                with SessionLocal() as db:
                    keywords = db.scalars(select(Keyword).where(Keyword.project_id == project.id).order_by(Keyword.opportunity_score.desc())).all()
            await self._step(task_id, "schedule_keywords", f"已按机会分排序 {len(keywords)} 个关键词", 0.12)
            scan_keywords = keywords if full else keywords[:8]
            videos_seen = 0
            comments_seen = 0
            leads_seen = 0
            for index, keyword in enumerate(scan_keywords):
                await self._step(task_id, "scan_keyword", f"正在扫描：{keyword.keyword}", 0.08)
                videos = await self.provider.search_videos(keyword.keyword, 20 if full else 2)
                with SessionLocal() as db:
                    for dto in videos:
                        existing = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform == dto.platform, Video.platform_video_id == dto.video_id))
                        if existing:
                            video = existing
                        else:
                            video = Video(project_id=project.id, platform=dto.platform, platform_video_id=dto.video_id, title=dto.title, description=dto.description, creator=dto.creator, url=dto.url, cover=dto.cover, publish_time=dto.publish_time, likes=dto.likes, comments=dto.comments, shares=dto.shares, collects=dto.collects, keyword=dto.keyword, opportunity_score=_video_score(dto.likes, dto.comments, index), level=lead_level(_video_score(dto.likes, dto.comments, index)))
                            db.add(video)
                        keyword.video_count += 1
                        keyword.last_scanned_at = now_utc()
                    db.commit()
                videos_seen += len(videos)
                await self.emit(task_id, project.id, "video.discovered", f"发现 {len(videos)} 个相关视频", {"keyword": keyword.keyword, "count": len(videos)})
                for dto in videos:
                    result = await self.provider.get_comments(dto.video_id)
                    comments_seen += result.items_received
                    await self.emit(task_id, project.id, "comment.discovered", f"发现 {result.items_received} 条公开评论（覆盖范围：{result.coverage_status}）", {"coverage_status": result.coverage_status, "count": result.items_received})
                    for comment_dto in result.items:
                        if not self.prefilter.should_analyze(comment_dto.content):
                            continue
                        with SessionLocal() as db:
                            video = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                            if not video:
                                continue
                            c = db.scalar(select(Comment).where(Comment.project_id == project.id, Comment.platform == comment_dto.platform, Comment.platform_comment_id == comment_dto.comment_id))
                            if not c:
                                c = Comment(project_id=project.id, video_id=video.id, platform=comment_dto.platform, platform_comment_id=comment_dto.comment_id, platform_user_id=comment_dto.user_id, nickname=comment_dto.nickname, profile_url=comment_dto.profile_url, content=comment_dto.content, content_hash=fingerprint(comment_dto.content), created_at_platform=comment_dto.created_at, coverage_status=result.coverage_status)
                                db.add(c)
                                db.flush()
                            project_data = {"industry": project.industry, "location": project.location, "service": project.service}
                            judgment = await self.judge_agent.run(project_data, {"content": c.content, "nickname": c.nickname})
                            if judgment["is_lead"]:
                                lead = _upsert_lead(db, project.id, c, judgment, video.id)
                                leads_seen += 1
                            db.commit()
                await self._checkpoint(task_id, keyword.id, 0, "")
            await self._step(task_id, "discover_videos", f"累计发现 {videos_seen} 个视频", 0.08)
            await self._step(task_id, "rank_videos", "已完成视频机会评分与分级", 0.08)
            await self._step(task_id, "scan_comments", f"累计读取 {comments_seen} 条公开评论", 0.08)
            await self._step(task_id, "prefilter_comments", "已过滤无意义、重复和明显无关评论", 0.08)
            await self._step(task_id, "judge_leads", f"AI 已判断 {leads_seen} 个潜客信号", 0.08)
            await self._step(task_id, "deduplicate_leads", "已按平台用户 ID 合并重复出现", 0.08)
            await self._step(task_id, "update_dashboard", "雷达数据已更新", 0.08)
            with SessionLocal() as db:
                task = db.get(ScanTask, task_id)
                task.status, task.finished_at, task.current_step = "completed", now_utc(), "update_dashboard"
                metrics = {"videos": videos_seen, "comments": comments_seen, "leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id)) or 0, "s_leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.lead_level == "S")) or 0}
                db.add(TaskReport(task_id=task_id, summary="行业扫描已完成", metrics=metrics))
                project.status = "running"
                db.commit()
            await self.emit(task_id, project.id, "task.completed", f"扫描完成：发现 {metrics['leads']} 个潜客，其中 {metrics['s_leads']} 个 S 级", metrics)
        except TaskPaused:
            await self.emit(task_id, project.id, "task.paused", "任务已暂停，进度已保存到 checkpoint")
        except Exception as exc:
            with SessionLocal() as db:
                task = db.get(ScanTask, task_id)
                task.status, task.error, task.finished_at = "failed", str(exc), now_utc()
                db.commit()
            await self.emit(task_id, project.id, "task.failed", f"任务失败：{exc}")

    async def _step(self, task_id, name, message, delay):
        if self._is_paused(task_id):
            raise TaskPaused()
        with SessionLocal() as db:
            step = db.scalar(select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.name == name))
            task = db.get(ScanTask, task_id)
            if step:
                step.status, step.started_at, step.detail = "running", now_utc(), message
                task.current_step = name
                db.commit()
        await self.emit(task_id, task.project_id, f"step.{name}.started", message)
        await asyncio.sleep(delay)
        if self._is_paused(task_id):
            raise TaskPaused()
        with SessionLocal() as db:
            step = db.scalar(select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.name == name))
            if step:
                step.status, step.finished_at = "completed", now_utc()
                db.commit()
        await self.emit(task_id, task.project_id, f"step.{name}.completed", message)

    async def _checkpoint(self, task_id, keyword_id, video_id, cursor):
        with SessionLocal() as db:
            checkpoint = db.get(TaskCheckpoint, task_id)
            checkpoint.last_keyword_id = keyword_id
            checkpoint.last_video_id = video_id
            checkpoint.last_comment_cursor = cursor or ""
            db.commit()

    async def emit(self, task_id, project_id, event_type, message, payload=None):
        with SessionLocal() as db:
            event = TaskEvent(task_id=task_id, project_id=project_id, event_type=event_type, message=message, payload=payload or {})
            db.add(event)
            db.commit()
            event_data = {"id": event.id, "event_type": event.event_type, "message": event.message, "payload": event.payload, "created_at": event.created_at.isoformat()}
        await event_bus.publish(event_data)

    def _is_paused(self, task_id: int) -> bool:
        with SessionLocal() as db:
            task = db.get(ScanTask, task_id)
            return bool(task and task.status == "paused")


class TaskPaused(Exception):
    pass


def _upsert_lead(db, project_id, comment, judgment, video_id):
    from app.models import Lead
    lead = db.scalar(select(Lead).where(Lead.project_id == project_id, Lead.platform == comment.platform, Lead.platform_user_id == comment.platform_user_id)) if comment.platform_user_id else db.scalar(select(Lead).where(Lead.project_id == project_id, Lead.nickname == comment.nickname, Lead.profile_url == comment.profile_url))
    if not lead:
        lead = Lead(project_id=project_id, platform=comment.platform, platform_user_id=comment.platform_user_id, nickname=comment.nickname, profile_url=comment.profile_url, **{key: judgment[key] for key in ["lead_score", "lead_level", "intent_level", "need", "location", "budget", "purchase_stage", "pain_point", "buying_signals", "summary", "reason", "recommended_action"]})
        db.add(lead)
        db.flush()
    else:
        lead.lead_score = max(lead.lead_score, judgment["lead_score"])
        lead.lead_level = lead_level(lead.lead_score)
        lead.occurrence_count += 1
        lead.last_seen_at = now_utc()
    if not db.scalar(select(LeadComment).where(LeadComment.lead_id == lead.id, LeadComment.comment_id == comment.id)):
        db.add(LeadComment(lead_id=lead.id, comment_id=comment.id))
    if not db.scalar(select(LeadSource).where(LeadSource.lead_id == lead.id, LeadSource.video_id == video_id)):
        db.add(LeadSource(lead_id=lead.id, video_id=video_id))
    db.add(LeadEvent(lead_id=lead.id, score=lead.lead_score, event_type="detected", note=comment.content))
    return lead


def _video_score(likes, comments, index):
    return round(min(99, 56 + comments / 10 + likes / 900 - index * 0.5), 1)


def _category_counts(rows):
    return {category: sum(1 for row in rows if row["category"] == category) for category in sorted({row["category"] for row in rows})}
