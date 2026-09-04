from app.agents.keyword_agent import keyword_opportunity_score
from app.agents.lead_judge_agent import RulePreFilter
from app.providers.mock.mock_provider import MockProvider
from app.services.radar_service import fingerprint, lead_level
from app.services.event_bus import sse_line
from app.tasks.checkpoint import checkpoint_snapshot


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
