import json
from pathlib import Path

from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO


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
        report = self._read()
        raw = report.get("comments", [])
        if isinstance(raw, dict):
            raw = raw.get(video_id, [])
        if isinstance(raw, list):
            raw = [item for item in raw if not item.get("video_id") or str(item.get("video_id")) == video_id]
        offset = int(cursor or 0) if str(cursor or "0").isdigit() else 0
        page = raw[offset:offset + 100]
        items = []
        for item in page:
            comment_id = str(item.get("comment_id", item.get("cid", ""))).strip()
            if not comment_id:
                continue
            items.append(CommentDTO("douyin", comment_id, str(item.get("user_id", item.get("uid", ""))), str(item.get("nickname", item.get("author", ""))), str(item.get("profile_url", "")), str(item.get("content", item.get("comment", ""))), _parse_dt(item.get("create_time", item.get("created_at"))), str(item.get("parent_comment_id", item.get("reply_to", ""))), id_source="platform_field"))
        has_more = offset + len(page) < len(raw)
        return CommentScanResult(items, str(report.get("coverage_status", "partial" if raw else "unknown")), len(items), str(offset + len(page)) if has_more else None, has_more)

    def _read(self):
        if not self.report_path or not self.report_path.exists():
            return {}
        return json.loads(self.report_path.read_text(encoding="utf-8"))


def _parse_dt(value):
    from datetime import datetime
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except (TypeError, ValueError, OSError):
        return None
