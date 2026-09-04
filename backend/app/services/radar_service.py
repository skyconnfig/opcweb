import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.agents.industry_agent import IndustryAgent
from app.agents.keyword_agent import KeywordAgent, keyword_opportunity_score
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.llm import BaseLLMProvider, OpenAICompatibleProvider
from app.agents.persona_agent import PersonaAgent
from app.agents.radar_agent import RadarAgent
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import AgentRun, Comment, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Project, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, ScanTask, Video, now_utc
from app.providers.base import BaseContentProvider
from app.services.event_bus import event_bus
from app.tasks.queue import enqueue_scan


def fingerprint(content: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", content).lower().encode("utf-8")).hexdigest()


def lead_level(score: float) -> str:
    return "S" if score >= 90 else "A" if score >= 75 else "B" if score >= 60 else "C"


class RadarService:
    def __init__(self, provider: BaseContentProvider, llm: BaseLLMProvider | None = None):
        self.provider = provider
        self.llm = llm or OpenAICompatibleProvider(get_settings())
        self.industry_agent = IndustryAgent(self.llm)
        self.keyword_agent = KeywordAgent(self.llm)
        self.judge_agent = LeadJudgeAgent(self.llm)
        self.persona_agent = PersonaAgent(self.llm)
        self.radar_agent = RadarAgent()
        self.prefilter = RulePreFilter()

    async def analyze_project(self, project_id: int) -> dict:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if not project:
                raise ValueError("项目不存在")
            context = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description}
            self.llm.clear_last_call()
            try:
                intelligence = await self.industry_agent.run(context)
            except Exception:
                self._record_agent_run(db, project.id, "IndustryAgent", self.industry_agent.prompt_version, context, {})
                db.commit()
                raise
            project.intelligence = intelligence
            self._record_agent_run(db, project.id, "IndustryAgent", self.industry_agent.prompt_version, context, intelligence)
            self.llm.clear_last_call()
            try:
                keywords = await self.keyword_agent.run(context, intelligence)
            except Exception:
                self._record_agent_run(db, project.id, "KeywordAgent", self.keyword_agent.prompt_version, {"context": context, "intelligence": intelligence}, {})
                db.commit()
                raise
            self._record_agent_run(db, project.id, "KeywordAgent", self.keyword_agent.prompt_version, {"context": context, "intelligence": intelligence}, {"keywords": keywords})
            for row in keywords:
                existing = db.scalar(select(Keyword).where(Keyword.project_id == project.id, Keyword.keyword == row["keyword"]))
                if existing:
                    for key, value in row.items():
                        setattr(existing, key, value)
                else:
                    db.add(Keyword(project_id=project.id, **row))
            project.status = "ready"
            db.commit()
            return {"intelligence": intelligence, "keyword_count": len(keywords), "categories": _category_counts(keywords)}

    def _record_agent_run(self, db, project_id: int, agent: str, prompt_version: str, input_payload: dict, output: dict):
        call = self.llm.last_call
        input_text = call.input_text if call else json.dumps(input_payload, ensure_ascii=False)
        model = call.model if call else (self.llm.model if self.llm.configured else "deterministic-mock")
        db.add(AgentRun(project_id=project_id, agent=agent, model=model, prompt_version=prompt_version, input_hash=fingerprint(input_text), input_text=input_text, output=output, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=call.success if call else True, error=call.error if call else ""))

    async def start_scan(self, project_id: int, full: bool = False) -> int:
        with SessionLocal() as db:
            return enqueue_scan(db, project_id, full).id

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
            with SessionLocal() as db:
                checkpoint = db.get(TaskCheckpoint, task_id)
                last_keyword_id = checkpoint.last_keyword_id if checkpoint else 0
            remaining_keywords = [keyword for keyword in keywords if keyword.id > last_keyword_id]
            scan_keywords = remaining_keywords if full else remaining_keywords[:8]
            videos_seen = 0
            comments_seen = 0
            comments_prefiltered = 0
            comments_judged = 0
            leads_seen = 0
            with SessionLocal() as db:
                checkpoint = db.get(TaskCheckpoint, task_id)
                processed_comment_ids = set(checkpoint.processed_comment_ids or []) if checkpoint else set()
            seen_comment_hashes: set[str] = set()
            for index, keyword in enumerate(scan_keywords):
                await self._step(task_id, "scan_keyword", f"正在扫描：{keyword.keyword}", 0.08)
                videos = await self.provider.search_videos(keyword.keyword, 20 if full else 2)
                with SessionLocal() as db:
                    for dto in videos:
                        radar = self.radar_agent.score({"title": dto.title, "description": dto.description, "creator": dto.creator, "publish_time": dto.publish_time, "likes": dto.likes, "comments": dto.comments, "shares": dto.shares, "collects": dto.collects}, keyword.keyword)
                        existing = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform == dto.platform, Video.platform_video_id == dto.video_id))
                        if existing:
                            video = existing
                            video.industry_relevance_score = radar["industry_relevance_score"]
                            video.commercial_relevance_score = radar["commercial_relevance_score"]
                            video.lead_opportunity_score = radar["lead_opportunity_score"]
                            video.opportunity_score = radar["video_opportunity_score"]
                            video.level = radar["level"]
                        else:
                            video = Video(project_id=project.id, platform=dto.platform, platform_video_id=dto.video_id, title=dto.title, description=dto.description, creator=dto.creator, url=dto.url, cover=dto.cover, publish_time=dto.publish_time, likes=dto.likes, comments=dto.comments, shares=dto.shares, collects=dto.collects, keyword=dto.keyword, opportunity_score=radar["video_opportunity_score"], industry_relevance_score=radar["industry_relevance_score"], commercial_relevance_score=radar["commercial_relevance_score"], lead_opportunity_score=radar["lead_opportunity_score"], level=radar["level"])
                            db.add(video)
                        keyword.video_count += 1
                        keyword.last_scanned_at = now_utc()
                    db.commit()
                videos_seen += len(videos)
                await self.emit(task_id, project.id, "video.discovered", f"发现 {len(videos)} 个相关视频", {"keyword": keyword.keyword, "count": len(videos)})
                with SessionLocal() as db:
                    checkpoint = db.get(TaskCheckpoint, task_id)
                    resume_video_id = checkpoint.last_video_id if checkpoint and checkpoint.last_keyword_id < keyword.id else 0
                    resume_cursor = checkpoint.last_comment_cursor if resume_video_id else None
                for dto in videos:
                    with SessionLocal() as db:
                        video_row = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                        cursor = resume_cursor if video_row and video_row.id == resume_video_id else None
                    while True:
                        result = await self.provider.get_comments(dto.video_id, cursor)
                        comments_seen += result.items_received
                        await self.emit(task_id, project.id, "comment.discovered", f"发现 {result.items_received} 条公开评论（覆盖范围：{result.coverage_status}）", {"coverage_status": result.coverage_status, "count": result.items_received, "has_more": result.has_more})
                        for comment_dto in result.items:
                            if not self.prefilter.should_analyze(comment_dto.content, seen_comment_hashes):
                                comments_prefiltered += 1
                                continue
                            with SessionLocal() as db:
                                video = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                                if not video:
                                    continue
                                c = db.scalar(select(Comment).where(Comment.project_id == project.id, Comment.platform == comment_dto.platform, Comment.platform_comment_id == comment_dto.comment_id))
                                if not c:
                                    c = Comment(project_id=project.id, video_id=video.id, platform=comment_dto.platform, platform_comment_id=comment_dto.comment_id, platform_user_id=comment_dto.user_id, nickname=comment_dto.nickname, profile_url=comment_dto.profile_url, content=comment_dto.content, content_hash=fingerprint(comment_dto.content), parent_comment_id=comment_dto.parent_comment_id, created_at_platform=comment_dto.created_at, coverage_status=result.coverage_status)
                                    db.add(c)
                                    db.flush()
                                if c.id in processed_comment_ids:
                                    continue
                                comments_judged += 1
                                history_rows = db.scalars(select(Comment).where(Comment.project_id == project.id, Comment.platform == c.platform, Comment.platform_user_id == c.platform_user_id).order_by(Comment.id)).all() if c.platform_user_id else []
                                history_text = "\n".join(f"{index + 1}. {row.content}" for index, row in enumerate(history_rows))
                                project_data = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description, "keyword": keyword.keyword, "video_title": dto.title, "video_description": dto.description, "video_creator": dto.creator, "video_likes": dto.likes, "video_comments": dto.comments, "video_shares": dto.shares, "video_collects": dto.collects, "history_text": history_text}
                                comment_data = {"content": c.content, "nickname": c.nickname, "history_text": history_text, "parent_comment_id": c.parent_comment_id}
                                self.llm.clear_last_call()
                                try:
                                    judgment = await self.judge_agent.run(project_data, comment_data)
                                except Exception as exc:
                                    self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, {})
                                    db.commit()
                                    raise exc
                                self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, judgment)
                                if judgment["is_lead"]:
                                    _upsert_lead(db, project.id, c, judgment, video.id)
                                    leads_seen += 1
                                db.commit()
                                processed_comment_ids.add(c.id)
                                await self._checkpoint(task_id, None, video.id, result.next_cursor, c.id)
                        if not result.has_more or not result.next_cursor:
                            break
                        cursor = result.next_cursor
                await self._checkpoint(task_id, keyword.id, 0, "")
            await self._step(task_id, "discover_videos", f"累计发现 {videos_seen} 个视频", 0.08)
            await self._step(task_id, "rank_videos", "已完成视频机会评分与分级", 0.08)
            await self._step(task_id, "scan_comments", f"累计读取 {comments_seen} 条公开评论", 0.08)
            await self._step(task_id, "prefilter_comments", "已过滤无意义、重复和明显无关评论", 0.08)
            await self._step(task_id, "judge_leads", f"AI 已判断 {comments_judged} 条候选评论，发现 {leads_seen} 个潜客信号", 0.08)
            await self._step(task_id, "deduplicate_leads", "已按平台用户 ID 合并重复出现", 0.08)
            await self._step(task_id, "update_dashboard", "雷达数据已更新", 0.08)
            with SessionLocal() as db:
                task = db.get(ScanTask, task_id)
                task.status, task.finished_at, task.current_step = "completed", now_utc(), "update_dashboard"
                metrics = {"videos": db.scalar(select(func.count(Video.id)).where(Video.project_id == project.id)) or 0, "comments": db.scalar(select(func.count(Comment.id)).where(Comment.project_id == project.id)) or 0, "comments_received": comments_seen, "comments_prefiltered": comments_prefiltered, "comments_judged": comments_judged, "prefilter_ratio": round(comments_prefiltered / comments_seen, 4) if comments_seen else 0, "leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id)) or 0, "s_leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project.id, Lead.lead_level == "S")) or 0}
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
            if step and step.status == "completed":
                return
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

    async def _checkpoint(self, task_id, keyword_id, video_id, cursor, processed_comment_id=None):
        with SessionLocal() as db:
            checkpoint = db.get(TaskCheckpoint, task_id)
            if keyword_id is not None:
                checkpoint.last_keyword_id = keyword_id
            checkpoint.last_video_id = video_id
            checkpoint.last_comment_cursor = cursor or ""
            if processed_comment_id is not None:
                processed = list(checkpoint.processed_comment_ids or [])
                if processed_comment_id not in processed:
                    processed.append(processed_comment_id)
                checkpoint.processed_comment_ids = processed[-5000:]
            db.commit()

    async def emit(self, task_id, project_id, event_type, message, payload=None):
        with SessionLocal() as db:
            event = TaskEvent(task_id=task_id, project_id=project_id, event_type=event_type, message=message, payload=payload or {})
            db.add(event)
            db.commit()
            event_data = {"id": event.id, "project_id": event.project_id, "event_type": event.event_type, "message": event.message, "payload": event.payload, "created_at": event.created_at.isoformat()}
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
