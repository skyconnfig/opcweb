import json
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet

from app.agents.keyword_agent import keyword_opportunity_score
from app.agents.industry_agent import IndustryAgent
from app.agents.keyword_agent import KeywordAgent
from app.agents.lead_judge_agent import LeadJudgeAgent, RulePreFilter
from app.agents.llm import OpenAICompatibleProvider
from app.agents.radar_agent import RadarAgent
from app.core.config import Settings
from app.providers.mock.mock_provider import MockProvider
from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.external.social_harvest import SocialHarvestExternalProvider
from app.services.radar_service import fingerprint, lead_level
from app.services.event_bus import sse_line
from app.settings_store import decrypt_secret, encrypt_secret
from app.tasks.checkpoint import checkpoint_snapshot
from app.tasks.queue import advance_schedule


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


async def test_mock_provider_normalization_and_coverage():
    provider = MockProvider()
    health = await provider.health_check()
    videos = await provider.search_videos("长沙装修", 2)
    comments = await provider.get_comments(videos[0].video_id)
    assert health.status == "connected"
    assert videos[0].platform == "douyin"
    assert comments.coverage_status in {"unknown", "partial", "complete"}
    assert comments.items_received == len(comments.items)


def test_sse_and_checkpoint_contract():
    assert '"event_type": "lead.detected"' in sse_line({"event_type": "lead.detected", "message": "发现潜客"})
    class Checkpoint:
        last_keyword_id = 3
        last_video_id = 8
        last_comment_cursor = "cursor-2"
        processed_comment_ids = [1, 2]
    assert checkpoint_snapshot(Checkpoint()) == {"last_keyword_id": 3, "last_video_id": 8, "last_comment_cursor": "cursor-2", "processed_comment_ids": [1, 2]}


def test_scan_schedule_advances_with_a_safe_minimum_interval():
    class Schedule:
        interval_minutes = 5
        last_run_at = None
        next_run_at = None

    now = datetime.now(timezone.utc)
    schedule = Schedule()
    advance_schedule(schedule, now)
    assert schedule.last_run_at == now
    assert schedule.next_run_at == now + timedelta(minutes=15)


async def test_text_only_agent_chain_and_history_context():
    project = {"industry": "装修", "location": "长沙", "service": "旧房翻新", "target_customer": "准备装修的长沙业主", "price_range": "5万-30万", "description": "提供设计与施工"}
    intelligence = await IndustryAgent().run(project)
    assert {"industry_summary", "target_customer_profiles", "pain_points", "buying_triggers", "common_questions", "customer_language", "competitor_types", "search_strategy"} <= intelligence.keys()
    assert len(KeywordAgent().generate(project, intelligence)) >= 100
    judgment = await LeadJudgeAgent().run({**project, "history_text": "1. 长沙有没有？\n2. 120平大概多少钱？\n3. 年底准备装。"}, {"content": "年底准备装", "nickname": "业主", "history_text": "1. 长沙有没有？\n2. 120平大概多少钱？"})
    assert judgment["is_lead"] is True
    assert judgment["location"] == "长沙"
    assert judgment["time_requirement"] == "近期"


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


def test_llm_api_key_is_encrypted_at_rest():
    key = Fernet.generate_key().decode()
    settings = Settings(settings_encryption_key=key)
    stored = encrypt_secret("sk-secret", settings)
    assert stored.startswith("enc:v1:")
    assert "sk-secret" not in stored
    assert decrypt_secret(stored, settings) == "sk-secret"


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
        return httpx.Response(200, json={"success": True, "video_count": 1, "comment_count": 2, "comments": [{"comment": "长沙多少钱？"}, {"comment": "年底准备装"}]})

    provider = DouyinCommentsCrawlerExternalProvider("https://crawler.example", transport=httpx.MockTransport(handler))
    videos = await provider.search_videos("长沙装修", 5)
    page = await provider.get_comments(videos[0].video_id)
    assert len(videos) == 1
    assert videos[0].video_id.startswith("keyword-comments-")
    assert page.items_received == 2
    assert page.items[0].content == "长沙多少钱？"
    assert len(calls) == 1


async def test_douyin_crawler_uses_returned_video_url_for_comment_scan():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.url.path.endswith("/api/keyword/comments"):
            return httpx.Response(200, json={"videos": [{"video_id": "v1", "video_url": "https://www.douyin.com/video/v1", "title": "长沙装修预算"}]})
        return httpx.Response(200, json={"comments": [{"comment_id": "c1", "content": "长沙多少钱？"}]})

    provider = DouyinCommentsCrawlerExternalProvider("https://crawler.example", transport=httpx.MockTransport(handler))
    videos = await provider.search_videos("长沙装修", 1)
    await provider.get_comments(videos[0].video_id)
    assert len(calls) == 2
    assert json.loads(calls[1].content)["video_url"] == "https://www.douyin.com/video/v1"
