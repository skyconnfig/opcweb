from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select

from app.agents.keyword_agent import KeywordAgent
from app.db import Base, SessionLocal, engine
from app.models import Comment, Keyword, Lead, LeadComment, LeadEvent, LeadSource, Persona, Project, Video
from app.providers.mock.mock_provider import HIGH_COMMENTS, MockProvider, ORDINARY_COMMENTS
from app.services.radar_service import fingerprint, lead_level


def init_database():
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    with SessionLocal() as db:
        if db.scalar(select(Project.id).limit(1)):
            return
        project = Project(name="长沙装修", industry="装修", location="长沙", service="旧房翻新 / 全屋装修", price_range="5万-30万", target_customer="准备装修的长沙业主", description="专注本地旧房翻新和全屋装修，提供设计、施工与预算建议。", status="running")
        db.add(project)
        db.flush()
        db.add(Persona(project_id=project.id, name="长沙装修老李", identity="长沙本地装修设计师", experience="10年", location="长沙", tone="专业但不推销，像朋友给建议", strengths="熟悉长沙户型、预算与施工避坑", forbidden_words="上来加微信、虚假承诺、最低价、保证、夸大效果"))
        context = {"industry": project.industry, "location": project.location, "service": project.service, "target_customer": project.target_customer}
        keyword_rows = KeywordAgent().generate(context, {})
        for row in keyword_rows:
            db.add(Keyword(project_id=project.id, **row))
        now = datetime.now(timezone.utc)
        for video_index in range(1, 21):
            video = Video(project_id=project.id, platform="douyin", platform_video_id=f"mock-{video_index:03d}", title=f"长沙装修｜最容易踩的 {video_index % 8 + 3} 个坑", description="长沙本地装修公开 Demo 视频", creator=f"长沙装修观察员{video_index}", url=f"https://www.douyin.com/video/mock-{video_index:03d}", publish_time=now - timedelta(days=video_index), likes=1200 + video_index * 730, comments=80 + video_index * 17, shares=16 + video_index * 3, collects=40 + video_index * 4, keyword="长沙装修", opportunity_score=min(99, 73 + video_index * .7), level="A" if video_index < 8 else "B", lead_density=0.16)
            db.add(video)
            db.flush()
            for comment_index in range(15):
                global_index = (video_index - 1) * 15 + comment_index
                is_lead = global_index % 6 == 0
                content = HIGH_COMMENTS[global_index % len(HIGH_COMMENTS)] if is_lead else ORDINARY_COMMENTS[global_index % len(ORDINARY_COMMENTS)]
                user_id = f"buyer-{global_index:03d}" if is_lead else f"viewer-{global_index:03d}"
                nickname = ["装修小白", "想翻新的阿敏", "长沙业主老周", "望城小何", "小满准备装修", "被增项坑过", "岳麓新家", "预算15万"][global_index % 8] if is_lead else f"路过的朋友{global_index}"
                comment = Comment(project_id=project.id, video_id=video.id, platform="douyin", platform_comment_id=f"comment-{global_index:03d}", platform_user_id=user_id, nickname=nickname, profile_url=f"https://www.douyin.com/user/{user_id}", content=content, content_hash=fingerprint(content), coverage_status="partial")
                db.add(comment)
                db.flush()
                if is_lead:
                    lead_index = global_index // 6
                    score = 96 if lead_index < 15 else 82 if lead_index < 35 else 66
                    lead = Lead(project_id=project.id, platform="douyin", platform_user_id=user_id, nickname=nickname, profile_url=comment.profile_url, lead_score=score, lead_level=lead_level(score), intent_level="high", need="旧房翻新 / 全屋装修", location="长沙", budget="15万" if "15万" in content else "", purchase_stage="comparison", pain_point="担心增项与踩坑" if "坑" in content or "增项" in content else "", buying_signals=["明确询价", "表达真实需求"], summary=f"{nickname}正在认真了解装修方案。", reason="出现明确询价、地域、预算或联系方式信号。", recommended_action="priority_follow_up", occurrence_count=1)
                    db.add(lead)
                    db.flush()
                    db.add(LeadComment(lead_id=lead.id, comment_id=comment.id))
                    db.add(LeadSource(lead_id=lead.id, video_id=video.id))
                    db.add(LeadEvent(lead_id=lead.id, score=score, event_type="detected", note=content))
        db.commit()


def _ensure_schema():
    """Add fields introduced after the first SQLite demo database was created."""
    additions = {
        "videos": {
            "industry_relevance_score": "FLOAT DEFAULT 0",
            "commercial_relevance_score": "FLOAT DEFAULT 0",
            "lead_opportunity_score": "FLOAT DEFAULT 0",
        },
        "comments": {"parent_comment_id": "VARCHAR(120) DEFAULT ''"},
        "agent_runs": {
            "model": "VARCHAR(120) DEFAULT 'deterministic-mock'",
            "input_text": "TEXT DEFAULT ''",
            "error": "TEXT DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column not in existing:
                    connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
