import json
from pathlib import Path

from app.providers.base import BaseContentProvider, CommentScanResult, ProviderHealth, VideoDTO


class SocialHarvestExternalProvider(BaseContentProvider):
    name = "Social Harvest (external)"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": True, "comments": True, "sub_comments": True, "creator": True}

    def __init__(self, report_path: str):
        self.report_path = Path(report_path) if report_path else None

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth("connected", "已找到外部 task report") if self.report_path and self.report_path.exists() else ProviderHealth("disconnected", "未配置 task report 路径")

    async def search_videos(self, keyword: str, limit: int):
        return [VideoDTO("douyin", str(item.get("video_id", item.get("aweme_id", index))), str(item.get("title", "")), str(item.get("description", "")), str(item.get("creator", "")), str(item.get("url", "")), str(item.get("cover", "")), None, int(item.get("likes", 0)), int(item.get("comments", 0)), int(item.get("shares", 0)), int(item.get("collects", 0)), keyword) for index, item in enumerate(self._read().get("videos", []))][:limit]

    async def get_video(self, video_id: str):
        item = next((video for video in self._read().get("videos", []) if str(video.get("video_id", video.get("aweme_id", ""))) == video_id), None)
        return VideoDTO("douyin", video_id, str(item.get("title", "")), str(item.get("description", "")), str(item.get("creator", "")), str(item.get("url", "")), str(item.get("cover", "")), None, int(item.get("likes", 0)), int(item.get("comments", 0)), int(item.get("shares", 0)), int(item.get("collects", 0)), "") if item else None

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        return CommentScanResult([], "unknown", 0, None, False)

    def _read(self):
        if not self.report_path or not self.report_path.exists():
            return {}
        return json.loads(self.report_path.read_text(encoding="utf-8"))
