from datetime import datetime

import httpx

from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO


class DouyinCommentsCrawlerExternalProvider(BaseContentProvider):
    name = "Douyin Comments Crawler"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": False, "comments": True, "sub_comments": False, "creator": False}

    def __init__(self, base_url: str, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health_check(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/health")
            return ProviderHealth("connected", f"HTTP {response.status_code}") if response.is_success else ProviderHealth("unavailable", f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return ProviderHealth("disconnected", str(exc))

    async def search_videos(self, keyword: str, limit: int) -> list[VideoDTO]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/keyword/comments", json={"keyword": keyword, "max_videos": limit, "per_video_limit": 1})
            response.raise_for_status()
            payload = response.json()
        return [VideoDTO("douyin", f"external-{index}", f"{keyword} 相关视频", "", "未知作者", "", "", None, 0, 0, 0, 0, keyword) for index in range(int(payload.get("video_count", 0)))]

    async def get_video(self, video_id: str) -> VideoDTO | None:
        return None

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/video/comments", json={"video_url": video_id, "limit": 50})
            response.raise_for_status()
            payload = response.json()
        items = [CommentDTO("douyin", str(item.get("comment_id", index)), str(item.get("user_id", "")), str(item.get("nickname", "")), str(item.get("profile_url", "")), str(item.get("comment", item.get("content", ""))), _parse_dt(item.get("create_time")), str(item.get("parent_comment_id", ""))) for index, item in enumerate(payload.get("comments", []))]
        return CommentScanResult(items, "unknown", len(items), None, False)


def _parse_dt(value):
    try:
        return datetime.fromtimestamp(float(value)) if value else None
    except (TypeError, ValueError, OSError):
        return None
