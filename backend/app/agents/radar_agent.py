import math
import re
from datetime import datetime, timezone


class RadarAgent:
    """Score opportunity from text and public metadata only."""

    prompt_version = "radar_text_v1"

    def score(self, video: dict, keyword: str, historical_lead_density: float = 0) -> dict:
        title = str(video.get("title", ""))
        description = str(video.get("description", ""))
        creator = str(video.get("creator", video.get("author", "")))
        text = f"{title} {description} {keyword} {creator}"
        keyword_terms = [term for term in re.split(r"\s+|[/｜，,。！？!?]", keyword) if term]
        matched = sum(1 for term in keyword_terms if term in f"{title} {description}")
        industry_relevance = min(100, 55 + matched * 12 + (10 if keyword and keyword in text else 0))

        commercial_terms = ("多少钱", "预算", "报价", "推荐", "靠谱", "怎么选", "避坑", "增项", "联系", "能做吗")
        commercial_hits = sum(1 for term in commercial_terms if term in text)
        commercial_relevance = min(100, 52 + commercial_hits * 7)

        likes = max(0, int(video.get("likes", video.get("like_count", 0)) or 0))
        comments = max(0, int(video.get("comments", video.get("comment_count", 0)) or 0))
        shares = max(0, int(video.get("shares", video.get("share_count", 0)) or 0))
        collects = max(0, int(video.get("collects", video.get("collect_count", 0)) or 0))
        activity = min(100, 35 + math.log1p(comments) * 9 + math.log1p(shares + collects) * 4 + math.log1p(likes) * 2)
        recency = self._recency_score(video.get("publish_time"))
        lead_density = min(100, max(0, float(historical_lead_density or 0) * 100))
        opportunity = round(
            industry_relevance * 0.25
            + min(100, comments / 4 + activity * 0.4) * 0.20
            + recency * 0.15
            + activity * 0.10
            + commercial_relevance * 0.10
            + lead_density * 0.20,
            1,
        )
        return {
            "industry_relevance_score": round(industry_relevance, 1),
            "commercial_relevance_score": round(commercial_relevance, 1),
            "lead_opportunity_score": opportunity,
            "video_opportunity_score": opportunity,
            "level": "S" if opportunity >= 90 else "A" if opportunity >= 75 else "B" if opportunity >= 60 else "C",
        }

    @staticmethod
    def _recency_score(value) -> float:
        if not value:
            return 55
        try:
            published = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            days = max(0, (datetime.now(timezone.utc) - published).days)
            return max(20, 100 - min(80, days * 3))
        except (TypeError, ValueError):
            return 55
