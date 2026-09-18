import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.agents.industry_agent import IndustryAgent
from app.agents.keyword_agent import KeywordAgent, keyword_opportunity_score
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.llm import BaseLLMProvider, OpenAICompatibleProvider
from app.agents.persona_agent import PersonaAgent
from app.agents.radar_agent import RadarAgent
from app.agents.reply_agent import ReplyAgent
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import AgentRun, Comment, CommentReply, FollowTask, Keyword, KnowledgeEntry, Lead, LeadComment, LeadEvent, LeadSource, Persona, Project, ReplyPolicy, TaskArtifact, TaskCheckpoint, TaskEvent, TaskReport, TaskStep, ScanTask, Video, now_utc
from app.providers.base import BaseContentProvider
from app.providers.douyin.exceptions import DouyinVerificationRequired
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
        self.reply_agent = ReplyAgent(self.llm)
        self.prefilter = RulePreFilter()

    async def analyze_project(self, project_id: int, task_id: int | None = None) -> dict:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            if not project:
                raise ValueError("项目不存在")
            context = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description}
            self.llm.clear_last_call()
            try:
                intelligence = await self.industry_agent.run(context)
            except Exception as exc:
                self._record_agent_run(db, project.id, "IndustryAgent", self.industry_agent.prompt_version, context, {}, task_id=task_id, success=False, error=str(exc))
                db.commit()
                raise
            project.intelligence = intelligence
            self._record_agent_run(db, project.id, "IndustryAgent", self.industry_agent.prompt_version, context, intelligence, task_id=task_id)
            self.llm.clear_last_call()
            try:
                keywords = await self.keyword_agent.run(context, intelligence)
            except Exception as exc:
                self._record_agent_run(db, project.id, "KeywordAgent", self.keyword_agent.prompt_version, {"context": context, "intelligence": intelligence}, {}, task_id=task_id, success=False, error=str(exc))
                db.commit()
                raise
            self._record_agent_run(db, project.id, "KeywordAgent", self.keyword_agent.prompt_version, {"context": context, "intelligence": intelligence}, {"keywords": keywords}, task_id=task_id)
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

    def _record_agent_run(
        self,
        db,
        project_id: int,
        agent: str,
        prompt_version: str,
        input_payload: dict,
        output: dict,
        *,
        task_id: int | None = None,
        success: bool | None = None,
        error: str | None = None,
    ):
        call = self.llm.last_call
        input_text = call.input_text if call else json.dumps(input_payload, ensure_ascii=False, default=str)
        model = call.model if call else self.llm.model
        recorded_success = call.success if call and success is None else (True if success is None else success)
        recorded_error = call.error if call and error is None else (error or "")
        run = AgentRun(task_id=task_id, project_id=project_id, agent=agent, model=model, prompt_version=prompt_version, input_hash=fingerprint(input_text), input_text=input_text, output=output, latency_ms=call.latency_ms if call else 0, token_usage=call.tokens if call else 0, success=recorded_success, error=recorded_error)
        db.add(run)
        db.flush()
        if task_id is not None:
            _record_task_artifact(db, task_id, "agent_run", run.id, "created")

    async def start_scan(self, project_id: int, full: bool = False) -> int:
        with SessionLocal() as db:
            return enqueue_scan(db, project_id, full).id

    async def analyze_video_comments(self, db, video: Video, comments: list[Comment]) -> dict:
        """Run the text-only lead pipeline for comments from a manual sync.

        Manual search/sync is a supported entry point, not a raw data import.
        Keep it on the same rule-prefilter, thread-context, LeadJudge and
        review-draft path as scheduled scans, while leaving the caller-owned
        transaction and without inventing a scan task for a one-video action.
        """

        project = db.get(Project, video.project_id)
        if project is None:
            raise ValueError("项目不存在")
        analyzed = filtered = leads = 0
        errors: list[str] = []
        seen_hashes: set[str] = set()
        for comment in comments:
            if not self.prefilter.should_analyze(comment.content, seen_hashes):
                filtered += 1
                continue
            thread_ids = {value for value in (comment.platform_comment_id, comment.parent_comment_id) if value}
            thread_filter = [Comment.platform_comment_id.in_(thread_ids), Comment.parent_comment_id.in_(thread_ids)] if thread_ids else []
            history_filter = [Comment.platform_user_id == comment.platform_user_id] if comment.platform_user_id else []
            history_rows = db.scalars(
                select(Comment)
                .where(Comment.project_id == project.id, Comment.platform == comment.platform, or_(*history_filter, *thread_filter))
                .order_by(Comment.id)
            ).all() if (history_filter or thread_filter) else [comment]
            history_text = "\n".join(f"{index + 1}. {row.content}" for index, row in enumerate(history_rows))
            project_data = {
                "industry": project.industry,
                "location": project.location,
                "service": project.service,
                "target_customer": project.target_customer,
                "price_range": project.price_range,
                "description": project.description,
                "keyword": video.keyword,
                "video_title": video.title,
                "video_description": video.description,
                "video_creator": video.creator,
                "video_likes": video.likes,
                "video_comments": video.comments,
                "video_shares": video.shares,
                "video_collects": video.collects,
                "history_text": history_text,
            }
            comment_data = {"content": comment.content, "nickname": comment.nickname, "history_text": history_text, "parent_comment_id": comment.parent_comment_id}
            self.llm.clear_last_call()
            try:
                judgment = await self.judge_agent.run(project_data, comment_data)
            except Exception as exc:
                self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, {}, success=False, error=str(exc))
                errors.append(f"comment:{comment.id}: {exc}")
                db.commit()
                continue
            self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, judgment)
            analyzed += 1
            if judgment["is_lead"]:
                lead = _upsert_lead(db, project.id, comment, judgment, video.id)
                leads += 1
                await self._maybe_generate_reply_draft(db, project, video, comment, lead, project_data, comment_data, None)
            db.commit()
        return {"analyzed": analyzed, "filtered": filtered, "leads": leads, "errors": errors, "status": "PARTIAL" if errors else "SUCCESS"}

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
                await self.analyze_project(project.id, task_id=task_id)
                with SessionLocal() as db:
                    keywords = db.scalars(select(Keyword).where(Keyword.project_id == project.id).order_by(Keyword.opportunity_score.desc())).all()
            await self._step(task_id, "schedule_keywords", f"已按机会分排序 {len(keywords)} 个关键词", 0.12)
            with SessionLocal() as db:
                checkpoint = db.get(TaskCheckpoint, task_id)
                last_keyword_id = checkpoint.last_keyword_id if checkpoint else 0
            # Database ids do not necessarily follow the opportunity order.
            # Resume from the completed keyword's position in the sorted list
            # so a lower-id keyword later in that list is not skipped.
            remaining_keywords = _keywords_after_checkpoint(keywords, last_keyword_id)
            scan_keywords = remaining_keywords if full else remaining_keywords[:8]
            videos_seen = 0
            comments_seen = 0
            comments_prefiltered = 0
            comments_judged = 0
            leads_seen = 0
            coverage_statuses: set[str] = set()
            with SessionLocal() as db:
                checkpoint = db.get(TaskCheckpoint, task_id)
                processed_comment_ids = set(checkpoint.processed_comment_ids or []) if checkpoint else set()
            seen_comment_hashes: set[str] = set()
            for index, keyword in enumerate(scan_keywords):
                await self._step(task_id, "scan_keyword", f"正在扫描：{keyword.keyword}", 0.08)
                videos = await self.provider.search_videos(keyword.keyword, 20 if full else 2)
                with SessionLocal() as db:
                    historical_lead_density = _keyword_historical_lead_density(db, project.id, keyword.keyword)
                    for dto in videos:
                        radar = self.radar_agent.score({"title": dto.title, "description": dto.description, "creator": dto.creator, "publish_time": dto.publish_time, "likes": dto.likes, "comments": dto.comments, "shares": dto.shares, "collects": dto.collects}, keyword.keyword, historical_lead_density)
                        existing = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform == dto.platform, Video.platform_video_id == dto.video_id))
                        if existing:
                            video = existing
                            change_type = "updated"
                            video.title = dto.title
                            video.description = dto.description
                            video.creator = dto.creator
                            video.url = dto.url
                            video.cover = dto.cover
                            video.publish_time = dto.publish_time
                            video.likes = dto.likes
                            video.comments = dto.comments
                            video.shares = dto.shares
                            video.collects = dto.collects
                            video.keyword = dto.keyword
                            video.industry_relevance_score = radar["industry_relevance_score"]
                            video.commercial_relevance_score = radar["commercial_relevance_score"]
                            video.lead_opportunity_score = radar["lead_opportunity_score"]
                            video.opportunity_score = radar["video_opportunity_score"]
                            video.level = radar["level"]
                        else:
                            change_type = "created"
                            video = Video(project_id=project.id, platform=dto.platform, platform_video_id=dto.video_id, title=dto.title, description=dto.description, creator=dto.creator, url=dto.url, cover=dto.cover, publish_time=dto.publish_time, likes=dto.likes, comments=dto.comments, shares=dto.shares, collects=dto.collects, keyword=dto.keyword, opportunity_score=radar["video_opportunity_score"], industry_relevance_score=radar["industry_relevance_score"], commercial_relevance_score=radar["commercial_relevance_score"], lead_opportunity_score=radar["lead_opportunity_score"], level=radar["level"])
                            db.add(video)
                        video.task_id = task_id
                        db.flush()
                        _record_task_artifact(db, task_id, "video", video.id, change_type)
                        keyword.video_count += 1
                        keyword.last_scanned_at = now_utc()
                    db.commit()
                videos_seen += len(videos)
                await self.emit(task_id, project.id, "video.discovered", f"发现 {len(videos)} 个相关视频", {"keyword": keyword.keyword, "count": len(videos)})
                with SessionLocal() as db:
                    checkpoint = db.get(TaskCheckpoint, task_id)
                    resume_video_id = checkpoint.last_video_id if checkpoint and checkpoint.last_keyword_id < keyword.id else 0
                    resume_cursor = (checkpoint.last_comment_cursor or None) if resume_video_id else None
                for dto in videos:
                    with SessionLocal() as db:
                        video_row = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                        cursor = resume_cursor if video_row and video_row.id == resume_video_id else None
                    page_cursors: set[str | None] = set()
                    while True:
                        if cursor in page_cursors:
                            raise RuntimeError(f"评论分页游标未前进：{dto.video_id}")
                        page_cursors.add(cursor)
                        result = await self.provider.get_comments(dto.video_id, cursor)
                        page_item_count = len(result.items)
                        comments_seen += page_item_count
                        coverage_statuses.add(str(result.coverage_status or "unknown").lower())
                        await self.emit(task_id, project.id, "comment.discovered", f"发现 {page_item_count} 条公开评论（覆盖范围：{result.coverage_status}）", {"coverage_status": result.coverage_status, "count": page_item_count, "has_more": result.has_more})
                        for comment_dto in result.items:
                            # Persist the raw public comment before the rule
                            # gate.  Filtering only controls LLM spend; it
                            # must not erase the source record from the
                            # reviewable comment pool when a later model call
                            # fails or when the text is obvious noise.
                            with SessionLocal() as db:
                                 video = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                                 if video:
                                     existing = db.scalar(select(Comment).where(Comment.project_id == project.id, Comment.platform == comment_dto.platform, Comment.platform_comment_id == comment_dto.comment_id))
                                     if existing is None:
                                         existing = Comment(project_id=project.id, video_id=video.id, task_id=task_id, platform=comment_dto.platform, platform_comment_id=comment_dto.comment_id, platform_user_id=comment_dto.user_id, id_source=getattr(comment_dto, "id_source", "dom_attribute"), nickname=comment_dto.nickname, profile_url=comment_dto.profile_url, comment_url=getattr(comment_dto, "comment_url", ""), content=comment_dto.content, content_hash=fingerprint(comment_dto.content), parent_comment_id=comment_dto.parent_comment_id, is_reply=getattr(comment_dto, "is_reply", False), like_count=getattr(comment_dto, "like_count", 0), created_at_platform=comment_dto.created_at, coverage_status=result.coverage_status)
                                         db.add(existing)
                                         change_type = "created"
                                     else:
                                         change_type = "updated"
                                         existing.task_id = task_id
                                         existing.video_id = video.id
                                         existing.platform_user_id = comment_dto.user_id
                                         existing.id_source = getattr(comment_dto, "id_source", existing.id_source)
                                         existing.nickname = comment_dto.nickname
                                         existing.profile_url = comment_dto.profile_url
                                         existing.comment_url = getattr(comment_dto, "comment_url", existing.comment_url)
                                         existing.content = comment_dto.content
                                         existing.content_hash = fingerprint(comment_dto.content)
                                         existing.parent_comment_id = comment_dto.parent_comment_id
                                         existing.is_reply = getattr(comment_dto, "is_reply", existing.is_reply)
                                         existing.like_count = getattr(comment_dto, "like_count", existing.like_count)
                                         existing.created_at_platform = comment_dto.created_at
                                         existing.coverage_status = result.coverage_status
                                     db.flush()
                                     _record_task_artifact(db, task_id, "comment", existing.id, change_type)
                                     db.commit()
                            if not self.prefilter.should_analyze(comment_dto.content, seen_comment_hashes):
                                comments_prefiltered += 1
                                continue
                            with SessionLocal() as db:
                                video = db.scalar(select(Video).where(Video.project_id == project.id, Video.platform_video_id == dto.video_id))
                                if not video:
                                    continue
                                c = db.scalar(select(Comment).where(Comment.project_id == project.id, Comment.platform == comment_dto.platform, Comment.platform_comment_id == comment_dto.comment_id))
                                if not c:
                                    c = Comment(project_id=project.id, video_id=video.id, task_id=task_id, platform=comment_dto.platform, platform_comment_id=comment_dto.comment_id, platform_user_id=comment_dto.user_id, id_source=getattr(comment_dto, "id_source", "dom_attribute"), nickname=comment_dto.nickname, profile_url=comment_dto.profile_url, comment_url=getattr(comment_dto, "comment_url", ""), content=comment_dto.content, content_hash=fingerprint(comment_dto.content), parent_comment_id=comment_dto.parent_comment_id, is_reply=getattr(comment_dto, "is_reply", False), like_count=getattr(comment_dto, "like_count", 0), created_at_platform=comment_dto.created_at, coverage_status=result.coverage_status)
                                    db.add(c)
                                    db.flush()
                                    _record_task_artifact(db, task_id, "comment", c.id, "created")
                                if c.id in processed_comment_ids:
                                    continue
                                comments_judged += 1
                                thread_ids = {value for value in (c.platform_comment_id, c.parent_comment_id) if value}
                                thread_filter = [Comment.platform_comment_id.in_(thread_ids), Comment.parent_comment_id.in_(thread_ids)] if thread_ids else []
                                history_filter = [Comment.platform_user_id == c.platform_user_id] if c.platform_user_id else []
                                history_rows = db.scalars(select(Comment).where(Comment.project_id == project.id, Comment.platform == c.platform, or_(*history_filter, *thread_filter)).order_by(Comment.id)).all() if (history_filter or thread_filter) else [c]
                                history_text = "\n".join(f"{index + 1}. {row.content}" for index, row in enumerate(history_rows))
                                project_data = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer, "price_range": project.price_range, "description": project.description, "keyword": keyword.keyword, "video_title": dto.title, "video_description": dto.description, "video_creator": dto.creator, "video_likes": dto.likes, "video_comments": dto.comments, "video_shares": dto.shares, "video_collects": dto.collects, "history_text": history_text}
                                comment_data = {"content": c.content, "nickname": c.nickname, "history_text": history_text, "parent_comment_id": c.parent_comment_id}
                                self.llm.clear_last_call()
                                try:
                                    judgment = await self.judge_agent.run(project_data, comment_data)
                                except Exception as exc:
                                    self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, {}, task_id=task_id, success=False, error=str(exc))
                                    db.commit()
                                    raise exc
                                self._record_agent_run(db, project.id, "LeadJudgeAgent", self.judge_agent.prompt_version, {"project": project_data, "comment": comment_data}, judgment, task_id=task_id)
                                detected_lead = None
                                if judgment["is_lead"]:
                                    detected_lead = _upsert_lead(db, project.id, c, judgment, video.id, task_id=task_id)
                                    leads_seen += 1
                                    await self._maybe_generate_reply_draft(
                                        db,
                                        project,
                                        video,
                                        c,
                                        detected_lead,
                                        project_data,
                                        comment_data,
                                        task_id,
                                    )
                                db.commit()
                                await self.emit(
                                    task_id,
                                    project.id,
                                    "comment.analyzed",
                                    f"已完成评论意图判断：{c.nickname or '未知用户'}",
                                    {
                                        "comment_id": c.id,
                                        "is_lead": bool(judgment["is_lead"]),
                                        "lead_score": judgment.get("lead_score", 0),
                                    },
                                )
                                if detected_lead is not None:
                                    await self.emit(
                                        task_id,
                                        project.id,
                                        "lead.detected",
                                        f"发现潜客信号：{detected_lead.nickname or '未知用户'}",
                                        {
                                            "comment_id": c.id,
                                            "lead_id": detected_lead.id,
                                            "lead_score": detected_lead.lead_score,
                                            "lead_level": detected_lead.lead_level,
                                        },
                                    )
                                processed_comment_ids.add(c.id)
                                # Keep the cursor that produced this page
                                # until every item in the page is durable and
                                # judged.  Advancing to next_cursor per item
                                # would skip the rest of a page after a retry.
                                await self._checkpoint(task_id, None, video.id, cursor, c.id)
                        await self._checkpoint(task_id, None, video_row.id if video_row else video.id, result.next_cursor)
                        if result.has_more and not result.next_cursor:
                            raise RuntimeError(f"评论分页声明还有更多数据但未提供游标：{dto.video_id}")
                        if not result.has_more or not result.next_cursor:
                            break
                        if result.next_cursor == cursor:
                            raise RuntimeError(f"评论分页游标未前进：{dto.video_id}")
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
                _refresh_keyword_metrics(db, project.id)
                task = db.get(ScanTask, task_id)
                task.status, task.finished_at, task.current_step = "completed", now_utc(), "update_dashboard"
                metrics = _task_report_metrics(
                    db,
                    task_id,
                    project.id,
                    comments_received=comments_seen,
                    comments_prefiltered=comments_prefiltered,
                    coverage_statuses=coverage_statuses,
                )
                metrics["collection_status"] = "SUCCESS"
                _upsert_task_report(db, task_id, "行业扫描已完成", metrics)
                project.status = "running"
                db.commit()
            await self.emit(task_id, project.id, "task.completed", f"扫描完成：发现 {metrics['leads']} 个潜客，其中 {metrics['s_leads']} 个 S 级", metrics)
        except TaskPaused:
            await self.emit(task_id, project.id, "task.paused", "任务已暂停，进度已保存到 checkpoint")
        except DouyinVerificationRequired as exc:
            # A verification page is an expected operator checkpoint, not a
            # terminal task failure. Keep the browser/profile alive and leave
            # the durable cursor untouched so the worker can resume this same
            # keyword after the operator completes the real challenge.
            with SessionLocal() as db:
                task = db.get(ScanTask, task_id)
                if task is not None:
                    task.status = "verification_required"
                    task.error = exc.message
                    task.finished_at = None
                    metrics = _task_report_metrics(
                        db,
                        task_id,
                        project.id,
                        comments_received=comments_seen,
                        comments_prefiltered=comments_prefiltered,
                        coverage_statuses=coverage_statuses,
                    )
                    metrics.update(_collection_error_context(exc, partial=bool(videos_seen or comments_seen)))
                    metrics["collection_status"] = "VERIFICATION_REQUIRED"
                    _upsert_task_report(db, task_id, "等待人工完成抖音验证", metrics)
                    db.commit()
            await self.emit(
                task_id,
                project.id,
                "task.verification_required",
                "抖音需要人工验证；请保持当前 Chrome 不要关闭，完成验证后程序会自动继续",
                _collection_error_context(exc, partial=bool(videos_seen or comments_seen)),
            )
        except Exception as exc:
            with SessionLocal() as db:
                task = db.get(ScanTask, task_id)
                task.status, task.error, task.finished_at = "failed", str(exc), now_utc()
                metrics = _task_report_metrics(
                    db,
                    task_id,
                    project.id,
                    comments_received=comments_seen,
                    comments_prefiltered=comments_prefiltered,
                    coverage_statuses=coverage_statuses,
                )
                metrics["failure"] = str(exc)
                metrics.update(_collection_error_context(exc, partial=bool(videos_seen or comments_seen)))
                _upsert_task_report(db, task_id, "行业扫描未完成", metrics)
                db.commit()
            await self.emit(
                task_id,
                project.id,
                "task.failed",
                f"任务失败：{exc}",
                _collection_error_context(exc, partial=bool(videos_seen or comments_seen)),
            )

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

    async def _maybe_generate_reply_draft(
        self,
        db,
        project: Project,
        video: Video,
        comment: Comment,
        lead: Lead,
        project_data: dict,
        comment_data: dict,
        task_id: int,
    ) -> None:
        """Create a review-only text draft for an eligible detected lead.

        This is deliberately separate from the send path.  Enabling the
        project's auto-reply setting opts into draft generation, not an
        external side effect; a human must still approve and confirm the real
        platform send through the API.
        """

        policy = db.scalar(select(ReplyPolicy).where(ReplyPolicy.project_id == project.id))
        if not self._auto_reply_eligible(policy, lead):
            return

        existing = db.scalar(
            select(CommentReply).where(
                CommentReply.comment_id == comment.id,
                CommentReply.status.in_(("DRAFT", "WAITING_REVIEW", "APPROVED", "SENDING", "SENT", "SENT_UNVERIFIED", "VERIFIED", "FAILED")),
            )
        )
        if existing is not None:
            return

        persona = db.scalar(select(Persona).where(Persona.project_id == project.id))
        knowledge = db.scalars(
            select(KnowledgeEntry).where(
                KnowledgeEntry.project_id == project.id,
                KnowledgeEntry.enabled.is_(True),
            )
        ).all()
        previous = db.scalars(
            select(CommentReply).where(CommentReply.comment_id == comment.id).order_by(CommentReply.id)
        ).all()
        lead_data = {column.name: getattr(lead, column.name) for column in Lead.__table__.columns}
        persona_data = {column.name: getattr(persona, column.name) for column in Persona.__table__.columns} if persona else {}
        input_payload = {
            "project": project_data,
            "comment": comment_data,
            "lead": lead_data,
            "persona": persona_data,
        }
        self.llm.clear_last_call()
        try:
            decision = await self.reply_agent.run(
                project_data,
                comment_data,
                lead_data,
                persona_data,
                [
                    {"title": item.title, "content": item.content, "tags": item.tags, "enabled": item.enabled}
                    for item in knowledge
                ],
                [{"reply_text": item.reply_text, "status": item.status} for item in previous],
            )
        except Exception as exc:
            self._record_agent_run(
                db,
                project.id,
                "ReplyAgent",
                self.reply_agent.prompt_version,
                input_payload,
                {},
                task_id=task_id,
                success=False,
                error=str(exc),
            )
            return

        decision_data = decision.model_dump()
        self._record_agent_run(
            db,
            project.id,
            "ReplyAgent",
            self.reply_agent.prompt_version,
            input_payload,
            decision_data,
            task_id=task_id,
        )
        if not decision.should_reply and not decision.need_human_review:
            return

        reply_text = decision.reply_text.strip()
        reply = CommentReply(
            project_id=project.id,
            comment_id=comment.id,
            platform=comment.platform,
            reply_text=reply_text,
            reply_source="AI_AUTO",
            status="WAITING_REVIEW",
            generated_at=now_utc(),
            error_code=",".join(decision.risk_flags),
            error_message=decision.reason,
        )
        db.add(reply)
        db.flush()
        if task_id is not None:
            await self.emit(
                task_id,
                project.id,
                "reply.generated",
                "已生成 AI 回复草稿，等待人工审核",
                {
                    "comment_id": comment.id,
                    "reply_id": reply.id,
                    "has_reply_text": bool(reply_text),
                    "need_human_review": decision.need_human_review,
                },
            )

        db.commit()
        if task_id is not None:
            await self.emit(
                task_id,
                project.id,
                "reply.waiting_review",
                "AI 回复草稿已生成；自动发送已禁用，必须人工审核并确认后发送",
                {"comment_id": comment.id, "reply_id": reply.id, "code": "HUMAN_REVIEW_REQUIRED"},
            )

    @staticmethod
    def _auto_reply_eligible(policy: ReplyPolicy | None, lead: Lead) -> bool:
        if policy is None or not policy.enabled or not policy.auto_reply_enabled:
            return False
        # The first version has no trusted platform-ownership proof.  Keep
        # this opt-in fail-closed so enabling the setting cannot silently
        # expand the reply scope beyond verified own content.
        if policy.auto_reply_own_content_only:
            return False
        if float(lead.confidence or 0) < float(policy.minimum_confidence or 0):
            return False
        if float(lead.lead_score or 0) < float(policy.minimum_lead_score or 0):
            return False
        intent = str(lead.intent_level or "").strip().lower()
        blocked = {str(value).strip().lower() for value in (policy.blocked_intents or []) if str(value).strip()}
        allowed = {str(value).strip().lower() for value in (policy.allowed_intents or []) if str(value).strip()}
        return intent not in blocked and (not allowed or intent in allowed)

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


def _record_task_artifact(db, task_id: int, entity_type: str, entity_id: int, change_type: str) -> None:
    """Record one durable task-to-entity edge without duplicate rows.

    A mutable ``Video`` or ``Comment`` keeps only its latest ``task_id`` for
    fast navigation.  This history row is what makes scheduled rescans
    auditable: the same entity can be touched by many different tasks.
    """

    artifact = db.scalar(
        select(TaskArtifact).where(
            TaskArtifact.task_id == task_id,
            TaskArtifact.entity_type == entity_type,
            TaskArtifact.entity_id == entity_id,
        )
    )
    if artifact is None:
        db.add(TaskArtifact(task_id=task_id, entity_type=entity_type, entity_id=entity_id, change_type=change_type))
    elif artifact.change_type != "created" and change_type == "created":
        # Preserve the strongest fact if a retry first recorded an update and
        # later discovers that the row was actually created by this task.
        artifact.change_type = "created"


def _task_report_metrics(
    db,
    task_id: int,
    project_id: int,
    *,
    comments_received: int = 0,
    comments_prefiltered: int = 0,
    coverage_statuses: set[str] | None = None,
) -> dict:
    """Build metrics from task-owned rows, never from project totals alone."""

    artifacts = db.scalars(select(TaskArtifact).where(TaskArtifact.task_id == task_id)).all()
    counts = {
        (entity_type, change_type): sum(
            1 for item in artifacts if item.entity_type == entity_type and item.change_type == change_type
        )
        for entity_type in ("video", "comment", "lead")
        for change_type in ("created", "updated")
    }
    videos_new = counts[("video", "created")]
    videos_updated = counts[("video", "updated")]
    comments_new = counts[("comment", "created")]
    comments_updated = counts[("comment", "updated")]
    leads_new = counts[("lead", "created")]
    leads_updated = counts[("lead", "updated")]
    judgments = db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.task_id == task_id,
            AgentRun.agent == "LeadJudgeAgent",
        )
    ) or 0
    successful_judgments = db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.task_id == task_id,
            AgentRun.agent == "LeadJudgeAgent",
            AgentRun.success.is_(True),
        )
    ) or 0
    project_totals = {
        "videos": db.scalar(select(func.count(Video.id)).where(Video.project_id == project_id)) or 0,
        "comments": db.scalar(select(func.count(Comment.id)).where(Comment.project_id == project_id)) or 0,
        "leads": db.scalar(select(func.count(Lead.id)).where(Lead.project_id == project_id)) or 0,
    }
    statuses = {str(status or "unknown").lower() for status in (coverage_statuses or set())}
    return {
        # These four totals are the number of distinct records touched by this
        # task.  The explicit *_new / *_updated keys remove any ambiguity.
        "videos": videos_new + videos_updated,
        "videos_new": videos_new,
        "videos_updated": videos_updated,
        "comments": comments_new + comments_updated,
        "comments_new": comments_new,
        "comments_updated": comments_updated,
        "comments_received": comments_received or comments_new + comments_updated,
        "comments_prefiltered": comments_prefiltered,
        "comments_judged": successful_judgments,
        "judgments": judgments,
        "judgments_new": judgments,
        "judgments_updated": 0,
        "judgments_successful": successful_judgments,
        "judgments_failed": judgments - successful_judgments,
        "prefilter_ratio": round(comments_prefiltered / comments_received, 4) if comments_received else 0,
        "coverage_status": _aggregate_coverage_status(statuses),
        "coverage_statuses": sorted(statuses),
        "leads": leads_new + leads_updated,
        "leads_new": leads_new,
        "leads_updated": leads_updated,
        "s_leads": db.scalar(
            select(func.count(func.distinct(TaskArtifact.entity_id)))
            .select_from(TaskArtifact)
            .join(Lead, Lead.id == TaskArtifact.entity_id)
            .where(TaskArtifact.task_id == task_id, TaskArtifact.entity_type == "lead", Lead.lead_level == "S")
        ) or 0,
        "project_totals": project_totals,
    }


def _collection_error_context(exc: Exception, *, partial: bool = False) -> dict:
    """Return a durable, text-only classification for a provider failure.

    The task lifecycle remains ``failed`` so existing queue semantics stay
    stable.  ``collection_status`` distinguishes a clean provider failure
    from a partial collection and preserves the actionable DOM/login/risk
    boundary for the task report and event stream.
    """

    detail = getattr(exc, "detail", {}) or {}
    code = str(getattr(exc, "code", type(exc).__name__))
    status_by_code = {
        "DOUYIN_LOGIN_REQUIRED": "LOGIN_REQUIRED",
        "DOUYIN_LOGIN_EXPIRED": "LOGIN_REQUIRED",
        "DOUYIN_VERIFICATION_REQUIRED": "BLOCKED",
        "DOUYIN_SELECTOR_NOT_FOUND": "DOM_CHANGED",
        "DOUYIN_PAGE_PARSE_FAILED": "DOM_CHANGED",
    }
    status = status_by_code.get(code, "FAILED")
    if partial and status == "FAILED":
        status = "PARTIAL"
    return {
        "collection_status": status,
        "error_type": code,
        "error_message": str(getattr(exc, "message", str(exc))),
        "url": str(detail.get("url") or detail.get("video_url") or detail.get("target_url") or ""),
        "timestamp": now_utc().isoformat(),
    }


def _upsert_task_report(db, task_id: int, summary: str, metrics: dict) -> TaskReport:
    report = db.scalar(select(TaskReport).where(TaskReport.task_id == task_id))
    if report is None:
        report = TaskReport(task_id=task_id, summary=summary, metrics=metrics)
        db.add(report)
    else:
        report.summary = summary
        report.metrics = metrics
    return report


def _upsert_lead(db, project_id, comment, judgment, video_id, *, task_id: int | None = None):
    lead = None
    if comment.platform_user_id:
        lead = db.scalar(select(Lead).where(Lead.project_id == project_id, Lead.platform == comment.platform, Lead.platform_user_id == comment.platform_user_id))
    elif comment.nickname or comment.profile_url:
        lead = db.scalar(select(Lead).where(Lead.project_id == project_id, Lead.platform == comment.platform, Lead.nickname == comment.nickname, Lead.profile_url == comment.profile_url))
    else:
        # Anonymous comments have no reliable identity.  Reuse only the lead
        # already attached to this exact durable comment; never merge every
        # anonymous commenter into one synthetic person.
        lead = db.scalar(
            select(Lead)
            .join(LeadComment, LeadComment.lead_id == Lead.id)
            .where(Lead.project_id == project_id, LeadComment.comment_id == comment.id)
        )
    created = lead is None
    if not lead:
        lead = Lead(project_id=project_id, platform=comment.platform, platform_user_id=comment.platform_user_id, nickname=comment.nickname, profile_url=comment.profile_url, **{key: judgment.get(key) or "" for key in ["lead_score", "lead_level", "intent_level", "need", "location", "budget", "time_requirement", "purchase_stage", "pain_point", "buying_signals", "summary", "reason", "recommended_action"]}, confidence=float(judgment.get("confidence") or 0))
        db.add(lead)
        db.flush()
        association_exists = False
    else:
        association_exists = db.scalar(select(LeadComment).where(LeadComment.lead_id == lead.id, LeadComment.comment_id == comment.id)) is not None
        lead.confidence = max(float(lead.confidence or 0), float(judgment.get("confidence") or 0))
        lead.lead_score = max(float(lead.lead_score or 0), float(judgment["lead_score"]))
        lead.lead_level = lead_level(lead.lead_score)
        lead.last_seen_at = now_utc()
        if not association_exists:
            lead.occurrence_count += 1
        for key in ["intent_level", "need", "location", "budget", "time_requirement", "purchase_stage", "pain_point", "buying_signals", "summary", "reason", "recommended_action"]:
            value = judgment.get(key)
            if value not in (None, "", []):
                setattr(lead, key, value)
    if not association_exists:
        db.add(LeadComment(lead_id=lead.id, comment_id=comment.id))
        if not db.scalar(select(LeadSource).where(LeadSource.lead_id == lead.id, LeadSource.video_id == video_id)):
            db.add(LeadSource(lead_id=lead.id, video_id=video_id))
        db.add(LeadEvent(lead_id=lead.id, score=lead.lead_score, event_type="detected", note=comment.content))
    if task_id is not None:
        _record_task_artifact(db, task_id, "lead", lead.id, "created" if created else "updated")
    if created:
        db.add(
            FollowTask(
                lead_id=lead.id,
                project_id=project_id,
                content=f"首次跟进：{lead.need or '确认具体需求、预算和时间'}",
                deadline=_default_follow_deadline(),
            )
        )
    return lead


def _default_follow_deadline() -> datetime:
    """Schedule the default human follow-up for 10:00 China time tomorrow."""

    china_tz = timezone(timedelta(hours=8))
    tomorrow = (datetime.now(china_tz) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 2, 0)


def _video_score(likes, comments, index):
    return round(min(99, 56 + comments / 10 + likes / 900 - index * 0.5), 1)


def _category_counts(rows):
    return {category: sum(1 for row in rows if row["category"] == category) for category in sorted({row["category"] for row in rows})}


def _aggregate_coverage_status(statuses: set[str]) -> str:
    """Report the least-complete observed coverage without overstating it."""

    normalized = {str(status or "unknown").lower() for status in statuses}
    if not normalized:
        return "unknown"
    if "unknown" in normalized:
        return "unknown"
    if normalized == {"complete"}:
        return "complete"
    if "partial" in normalized:
        return "partial"
    return "partial"


def _keywords_after_checkpoint(keywords, last_keyword_id: int):
    """Return keywords after a checkpoint in the current opportunity order."""

    if not last_keyword_id:
        return keywords
    checkpoint_index = next((index for index, keyword in enumerate(keywords) if keyword.id == last_keyword_id), None)
    return keywords[checkpoint_index + 1:] if checkpoint_index is not None else keywords


def _keyword_historical_lead_density(db, project_id: int, keyword: str) -> float:
    """Return prior lead/comment density for a text keyword as a 0..1 value."""

    comments = db.scalar(
        select(func.count(Comment.id))
        .join(Video, Comment.video_id == Video.id)
        .where(Video.project_id == project_id, Video.keyword == keyword)
    ) or 0
    if not comments:
        return 0.0
    leads = db.scalar(
        select(func.count(func.distinct(LeadSource.lead_id)))
        .select_from(LeadSource)
        .join(Video, LeadSource.video_id == Video.id)
        .where(Video.project_id == project_id, Video.keyword == keyword)
    ) or 0
    return min(1.0, leads / comments)


def _refresh_keyword_metrics(db, project_id: int) -> None:
    """Reconcile counters used by the UI from the durable source tables."""

    rows = db.scalars(select(Keyword).where(Keyword.project_id == project_id)).all()
    for keyword in rows:
        keyword.video_count = db.scalar(
            select(func.count(Video.id)).where(Video.project_id == project_id, Video.keyword == keyword.keyword)
        ) or 0
        keyword.comment_count = db.scalar(
            select(func.count(Comment.id))
            .join(Video, Comment.video_id == Video.id)
            .where(Video.project_id == project_id, Video.keyword == keyword.keyword)
        ) or 0
        keyword.lead_count = db.scalar(
            select(func.count(func.distinct(LeadSource.lead_id)))
            .select_from(LeadSource)
            .join(Video, LeadSource.video_id == Video.id)
            .where(Video.project_id == project_id, Video.keyword == keyword.keyword)
        ) or 0
        keyword.s_count = db.scalar(
            select(func.count(func.distinct(LeadSource.lead_id)))
            .select_from(LeadSource)
            .join(Video, LeadSource.video_id == Video.id)
            .join(Lead, Lead.id == LeadSource.lead_id)
            .where(Video.project_id == project_id, Video.keyword == keyword.keyword, Lead.lead_level == "S")
        ) or 0
