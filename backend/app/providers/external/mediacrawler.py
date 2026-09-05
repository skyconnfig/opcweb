import json
import os
import subprocess

from app.providers.base import BaseContentProvider, CommentScanResult, ProviderHealth, VideoDTO


class MediaCrawlerExternalProvider(BaseContentProvider):
    name = "MediaCrawler (external)"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": True, "comments": True, "sub_comments": True, "creator": True}

    def __init__(self, executable_path: str):
        self.executable_path = executable_path

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth("connected", "外部 MediaCrawler 路径已配置") if self.executable_path and os.path.exists(self.executable_path) else ProviderHealth("disconnected", "未配置外部可执行入口")

    async def search_videos(self, keyword: str, limit: int):
        payload = self._run_external(["--platform", "dy", "--type", "search", "--keywords", keyword, "--max_items", str(limit)])
        return [VideoDTO("douyin", str(item.get("video_id", item.get("aweme_id", index))), str(item.get("title", "")), str(item.get("description", "")), str(item.get("nickname", item.get("creator", ""))), str(item.get("url", "")), str(item.get("cover", "")), None, int(item.get("likes", 0)), int(item.get("comments", 0)), int(item.get("shares", 0)), int(item.get("collects", 0)), keyword) for index, item in enumerate(payload.get("videos", []))][:limit]

    async def get_video(self, video_id: str):
        payload = self._run_external(["--platform", "dy", "--type", "detail", "--video", video_id])
        item = payload.get("video") or (payload.get("videos") or [None])[0]
        return _video(item, "") if item else None

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        from app.providers.base import CommentDTO
        args = ["--platform", "dy", "--type", "detail", "--video", video_id]
        if cursor:
            args.extend(["--cursor", cursor])
        payload = self._run_external(args)
        items = []
        for item in payload.get("comments", []):
            comment_id = str(item.get("comment_id", item.get("cid", ""))).strip()
            if not comment_id:
                continue
            items.append(CommentDTO("douyin", comment_id, str(item.get("user_id", item.get("uid", ""))), str(item.get("nickname", "")), str(item.get("profile_url", "")), str(item.get("content", item.get("comment", ""))), id_source="platform_field"))
        return CommentScanResult(items, str(payload.get("coverage_status", "partial" if items else "unknown")), len(items), payload.get("next_cursor"), bool(payload.get("has_more", False)))

    def _run_external(self, args: list[str]) -> dict:
        completed = subprocess.run([self.executable_path, *args], capture_output=True, text=True, timeout=60, check=True)
        return json.loads(completed.stdout or "{}")


def _video(item: dict, keyword: str) -> VideoDTO:
    return VideoDTO("douyin", str(item.get("video_id", item.get("aweme_id", item.get("id", "")))), str(item.get("title", item.get("desc", ""))), str(item.get("description", item.get("desc", ""))), str(item.get("nickname", item.get("creator", ""))), str(item.get("url", item.get("video_url", ""))), str(item.get("cover", item.get("cover_url", ""))), None, int(item.get("likes", item.get("digg_count", 0)) or 0), int(item.get("comments", item.get("comment_count", 0)) or 0), int(item.get("shares", item.get("share_count", 0)) or 0), int(item.get("collects", item.get("collect_count", 0)) or 0), keyword)
