import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.providers.douyin.dto import DouyinCommentDTO
from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO


class DouyinCommentsCrawlerExternalProvider(BaseContentProvider):
    name = "Douyin Comments Crawler"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": False, "comments": True, "sub_comments": False, "creator": False}

    def __init__(self, base_url: str, timeout: float = 8.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self._comment_cache: dict[tuple[str, str | None], CommentScanResult] = {}
        self._video_urls: dict[str, str] = {}
        self._collection_lock = asyncio.Lock()
        self.last_collection_trace: dict[str, object] = {}

    async def health_check(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/health")
            return ProviderHealth("connected", f"HTTP {response.status_code}") if response.is_success else ProviderHealth("unavailable", f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return ProviderHealth("disconnected", str(exc))

    async def search_videos(self, keyword: str, limit: int) -> list[VideoDTO]:
        if not keyword.strip():
            raise ValueError("关键词不能为空")
        if limit < 1:
            raise ValueError("视频数量必须大于 0")

        # The local crawler adapter exposes a real search task and a separate
        # video data endpoint. Use that contract first so every returned video
        # has a source URL before comments are requested.
        async with self._collection_lock:
            # A scheduled scan must fetch fresh public comments. Keep the
            # per-cursor cache for one active collection, but never let a
            # previous 10-30 minute run become the next run's source.
            self._comment_cache.clear()
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                start = await client.post(
                    f"{self.base_url}/api/collect/search",
                    json={"keyword": keyword, "search_type": "video", "scroll_count": 8, "delay": 1.0},
                )
                if start.status_code == 404:
                    # The documented standalone crawler exposes a single
                    # keyword->comments endpoint instead. It is still a real
                    # DOM-backed source, but it does not manufacture video
                    # metadata; only URLs present in its response are used.
                    response = await client.post(
                        f"{self.base_url}/api/keyword/comments",
                        json={"keyword": keyword, "max_videos": limit, "per_video_limit": 50},
                    )
                    response.raise_for_status()
                    return self._videos_from_keyword_payload(response.json(), keyword, limit)
                start.raise_for_status()
                start_payload = start.json()
                task_id = str(start_payload.get("task_id", ""))
                if not task_id:
                    raise RuntimeError("外部采集器未返回真实搜索任务 ID")
                self.last_collection_trace = {
                    "search_endpoint": "/api/collect/search",
                    "task_id": task_id,
                    "search_started_at": datetime.now(timezone.utc).isoformat(),
                }
                status_payload = await self._wait_for_collection(client, task_id)
                self.last_collection_trace["search_completed_at"] = datetime.now(timezone.utc).isoformat()
                # The status response is task-scoped.  Reading the crawler's
                # legacy singleton data endpoint here would allow concurrent
                # keyword searches to cross-contaminate their video results.
                data_payload = status_payload

        rows = data_payload.get("data") or data_payload.get("videos") or data_payload.get("items") or []
        videos = [_video(item, keyword) for item in rows[:limit] if isinstance(item, dict) and _video_url(item)]
        if not videos:
            raise RuntimeError("外部采集器搜索任务完成，但没有返回可追溯的视频 URL")
        self._video_urls.update({video.video_id: video.url for video in videos if video.url})
        return videos

    def _videos_from_keyword_payload(self, payload: dict, keyword: str, limit: int) -> list[VideoDTO]:
        """Adapt the documented keyword endpoint without inventing metadata."""

        rows = payload.get("videos") or payload.get("items") or []
        videos = [
            _video(item, keyword)
            for item in rows[:limit]
            if isinstance(item, dict) and _video_url(item)
        ]
        if not videos:
            grouped: dict[str, dict] = {}
            for raw_item in payload.get("comments") or []:
                if not isinstance(raw_item, dict):
                    continue
                url = _video_url(raw_item)
                if not url:
                    continue
                grouped.setdefault(url, {"url": url, "title": "", "description": "", "creator": ""})
            videos = [_video(item, keyword) for item in list(grouped.values())[:limit]]
        if not videos:
            raise RuntimeError("外部采集器关键词任务完成，但响应没有真实视频 URL")
        comments = self._comments_from_payload(payload)
        unscoped = [item for item in comments.items if not self._comment_video_url(item)]
        for video in videos:
            self._video_urls[video.video_id] = video.url
            scoped = [item for item in comments.items if self._comment_video_url(item) == video.url]
            if len(videos) == 1:
                scoped.extend(unscoped)
            if scoped:
                self._comment_cache[(video.video_id, None)] = CommentScanResult(
                    scoped,
                    comments.coverage_status,
                    len(scoped),
                    comments.next_cursor,
                    comments.has_more,
                )
        return videos

    @staticmethod
    def _comment_video_url(comment: CommentDTO) -> str:
        return str(getattr(comment, "video_url", "") or "")

    async def _wait_for_collection(self, client: httpx.AsyncClient, task_id: str) -> dict:
        deadline = asyncio.get_running_loop().time() + max(self.timeout, 8.0) * 15
        poll_count = 0
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"外部采集器搜索任务超时: {task_id}")
            response = await client.get(f"{self.base_url}/api/collect/status/{task_id}")
            response.raise_for_status()
            poll_count += 1
            payload = response.json()
            status = str(payload.get("status", "")).lower()
            if status in {"completed", "complete", "success"}:
                self.last_collection_trace.update({"task_status": status, "poll_count": poll_count})
                return payload
            if status in {"failed", "error", "stopped"}:
                self.last_collection_trace.update({"task_status": status, "poll_count": poll_count})
                raise RuntimeError(f"外部采集器搜索任务失败: {payload.get('message', status)}")
            await asyncio.sleep(0.5)

    async def get_video(self, video_id: str) -> VideoDTO | None:
        return None

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        cache_key = (video_id, cursor)
        cached = self._comment_cache.get(cache_key)
        if cached is not None:
            return cached
        # The external crawler opens a real browser and waits for lazy-loaded
        # comments.  That can exceed the short health/search timeout even
        # when the saved session is valid.
        # The durable application database stores the platform video ID and
        # URL separately. After an API restart the in-memory URL map is empty,
        # so construct the canonical public URL instead of sending a bare ID
        # to the crawler's URL-based comments endpoint.
        video_url = self._video_urls.get(video_id) or (
            video_id if str(video_id).startswith(("http://", "https://")) else f"https://www.douyin.com/video/{video_id}"
        )
        async with self._collection_lock:
            async with httpx.AsyncClient(timeout=max(self.timeout, 90.0), transport=self.transport) as client:
                response = await client.post(f"{self.base_url}/api/video/comments", json={"video_url": video_url, "limit": 50, "cursor": cursor})
                response.raise_for_status()
                payload = response.json()
        result = self._comments_from_payload(payload)
        self._comment_cache[cache_key] = result
        return result

    @staticmethod
    def _comments_from_payload(payload: dict) -> CommentScanResult:
        raw_items = payload.get("comments") or payload.get("items") or []
        items = []
        for raw_item in raw_items:
            item = raw_item if isinstance(raw_item, dict) else {"content": raw_item}
            content = str(item.get("comment", item.get("content", "")))
            comment_id = str(item.get("comment_id", item.get("cid", ""))).strip()
            if not comment_id:
                continue
            items.append(DouyinCommentDTO(platform="douyin", comment_id=comment_id, user_id=str(item.get("user_id", item.get("uid", ""))), nickname=str(item.get("nickname", item.get("author", ""))), profile_url=str(item.get("profile_url", "")), content=content, created_at=_parse_dt(item.get("create_time", item.get("created_at"))), parent_comment_id=str(item.get("parent_comment_id", "")), id_source="platform_field", video_url=_video_url(item), comment_url=_comment_url(item)))
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
    url = _video_url(item)
    raw_video_id = str(item.get("video_id", item.get("aweme_id", item.get("id", "")))).strip()
    video_id = raw_video_id or _video_id_from_url(url) or url
    return VideoDTO("douyin", video_id, str(item.get("title", item.get("desc", ""))), str(item.get("description", item.get("desc", ""))), str(item.get("nickname", item.get("author", item.get("creator", "")))), url, str(item.get("cover", item.get("cover_url", item.get("cover_image", "")))), None, _number(item.get("likes", item.get("digg_count", 0))), _number(item.get("comments", item.get("comment_count", 0))), _number(item.get("shares", item.get("share_count", 0))), _number(item.get("collects", item.get("collect_count", 0))), keyword)


def _video_url(item: dict) -> str:
    value = str(item.get("url", item.get("video_url", ""))).strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return urljoin("https://www.douyin.com", value)
    return value


def _comment_url(item: dict) -> str:
    value = str(item.get("comment_url", item.get("url", ""))).strip()
    if "/comment/" not in value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return urljoin("https://www.douyin.com", value)
    return value


def _video_id_from_url(value: str) -> str:
    path = urlparse(value).path.rstrip("/")
    match = re.search(r"/video/([^/]+)$", path)
    return match.group(1) if match else ""


def _number(value) -> int:
    text = str(value or "0").replace(",", "").strip()
    multiplier = 1
    if text.endswith("万"):
        multiplier, text = 10_000, text[:-1]
    elif text.endswith("亿"):
        multiplier, text = 100_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0
