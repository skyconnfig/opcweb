import hashlib
from datetime import datetime, timezone

import httpx

from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO


class DouyinCommentsCrawlerExternalProvider(BaseContentProvider):
    name = "Douyin Comments Crawler"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": False, "comments": True, "sub_comments": False, "creator": False}

    def __init__(self, base_url: str, timeout: float = 8.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self._comment_cache: dict[str, CommentScanResult] = {}
        self._video_urls: dict[str, str] = {}

    async def health_check(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/health")
            return ProviderHealth("connected", f"HTTP {response.status_code}") if response.is_success else ProviderHealth("unavailable", f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return ProviderHealth("disconnected", str(exc))

    async def search_videos(self, keyword: str, limit: int) -> list[VideoDTO]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/api/keyword/comments", json={"keyword": keyword, "max_videos": limit, "per_video_limit": 1})
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("videos") or payload.get("items") or []
        if not rows:
            # The reference service returns a keyword-level comment collection by
            # default, not video URLs. Keep it usable without pretending that
            # each comment belongs to a discovered video.
            video_id = f"keyword-comments-{hashlib.sha256(keyword.encode('utf-8')).hexdigest()[:16]}"
            video = VideoDTO("douyin", video_id, f"{keyword}｜关键词公开评论集合", f"外部采集器按关键词返回的公开评论集合：{keyword}", "关键词评论集合", "", "", None, 0, int(payload.get("comment_count", len(payload.get("comments", []))) or 0), 0, 0, keyword)
            self._comment_cache[video_id] = self._comments_from_payload(payload)
            return [video]
        videos = [_video(item, keyword) for item in rows[:limit]]
        self._video_urls.update({video.video_id: video.url for video in videos if video.url})
        return videos

    async def get_video(self, video_id: str) -> VideoDTO | None:
        return None

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        cached = self._comment_cache.get(video_id)
        if cached is not None:
            return cached if not cursor else CommentScanResult([], cached.coverage_status, 0, None, False)
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(f"{self.base_url}/api/video/comments", json={"video_url": self._video_urls.get(video_id, video_id), "limit": 50, "cursor": cursor})
            response.raise_for_status()
            payload = response.json()
        return self._comments_from_payload(payload)

    @staticmethod
    def _comments_from_payload(payload: dict) -> CommentScanResult:
        raw_items = payload.get("comments") or payload.get("items") or []
        items = []
        for index, raw_item in enumerate(raw_items):
            item = raw_item if isinstance(raw_item, dict) else {"content": raw_item}
            content = str(item.get("comment", item.get("content", "")))
            fallback_id = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] + f"-{index}"
            items.append(CommentDTO("douyin", str(item.get("comment_id", item.get("cid", fallback_id))), str(item.get("user_id", item.get("uid", ""))), str(item.get("nickname", item.get("author", ""))), str(item.get("profile_url", "")), content, _parse_dt(item.get("create_time", item.get("created_at"))), str(item.get("parent_comment_id", ""))))
        return CommentScanResult(items, str(payload.get("coverage_status", "partial" if items else "unknown")), len(items), payload.get("next_cursor"), bool(payload.get("has_more", False)))


def _parse_dt(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError, OSError, OverflowError):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None


def _video(item: dict, keyword: str) -> VideoDTO:
    url = str(item.get("url", item.get("video_url", "")))
    return VideoDTO("douyin", str(item.get("video_id", item.get("aweme_id", item.get("id", url)))), str(item.get("title", item.get("desc", ""))), str(item.get("description", item.get("desc", ""))), str(item.get("nickname", item.get("creator", "未知作者"))), url, str(item.get("cover", item.get("cover_url", ""))), None, int(item.get("likes", item.get("digg_count", 0)) or 0), int(item.get("comments", item.get("comment_count", 0)) or 0), int(item.get("shares", item.get("share_count", 0)) or 0), int(item.get("collects", item.get("collect_count", 0)) or 0), keyword)
