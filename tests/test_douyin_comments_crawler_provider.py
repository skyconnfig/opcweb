import json

import httpx
import pytest

from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider
from app.providers.external.douyin_comments_crawler import _comment_url, _video, _video_url


def test_crawler_protocol_relative_video_url_is_normalized():
    assert _video_url({"url": "//www.douyin.com/video/123"}) == "https://www.douyin.com/video/123"


def test_crawler_video_id_is_derived_from_normalized_url():
    video = _video({"video_url": "//www.douyin.com/video/123", "title": "真实标题"}, "关键词")

    assert video.video_id == "123"
    assert video.url == "https://www.douyin.com/video/123"


def test_crawler_comment_url_only_accepts_comment_links():
    assert _comment_url({"comment_url": "//www.douyin.com/comment/456"}) == "https://www.douyin.com/comment/456"
    assert _comment_url({"url": "https://www.douyin.com/video/123"}) == ""


@pytest.mark.asyncio
async def test_documented_keyword_endpoint_keeps_real_video_and_comment_provenance():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/collect/search":
            return httpx.Response(404)
        if request.url.path == "/api/keyword/comments":
            return httpx.Response(
                200,
                json={
                    "coverage_status": "partial",
                    "comments": [
                        {
                            "comment_id": "comment-1",
                            "nickname": "真实用户",
                            "content": "长沙有没有？",
                            "video_url": "https://www.douyin.com/video/123",
                            "comment_url": "https://www.douyin.com/comment/comment-1",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected endpoint: {request.url}")

    provider = DouyinCommentsCrawlerExternalProvider(
        "http://crawler.test",
        transport=httpx.MockTransport(handler),
    )

    videos = await provider.search_videos("长沙装修", 1)
    comments = await provider.get_comments(videos[0].video_id)

    assert videos[0].video_id == "123"
    assert videos[0].url == "https://www.douyin.com/video/123"
    assert comments.items_received == 1
    assert comments.items[0].comment_id == "comment-1"
    assert comments.items[0].id_source == "platform_field"
    assert comments.items[0].comment_url == "https://www.douyin.com/comment/comment-1"


@pytest.mark.asyncio
async def test_search_uses_task_scoped_status_data_instead_of_global_video_store():
    requested_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/collect/search":
            return httpx.Response(200, json={"task_id": "task-a"})
        if request.url.path == "/api/collect/status/task-a":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-a",
                    "status": "completed",
                    "progress": 100,
                    "collected_count": 1,
                    "message": "采集完成",
                    "data": [
                        {
                            "video_id": "video-from-task-a",
                            "video_url": "https://www.douyin.com/video/task-a",
                            "title": "任务 A 的视频",
                        }
                    ],
                },
            )
        if request.url.path == "/api/data/videos":
            raise AssertionError("search must not read the shared global video store")
        raise AssertionError(f"unexpected endpoint: {request.url}")

    provider = DouyinCommentsCrawlerExternalProvider(
        "http://crawler.test",
        transport=httpx.MockTransport(handler),
    )

    videos = await provider.search_videos("长沙装修", 1)

    assert videos[0].video_id == "video-from-task-a"
    assert "/api/data/videos" not in requested_paths


@pytest.mark.asyncio
async def test_comment_sync_reconstructs_video_url_after_provider_restart():
    request_bodies = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/video/comments":
            request_bodies.append(request.read())
            return httpx.Response(200, json={"comments": []})
        raise AssertionError(f"unexpected endpoint: {request.url}")

    provider = DouyinCommentsCrawlerExternalProvider(
        "http://crawler.test",
        transport=httpx.MockTransport(handler),
    )

    await provider.get_comments("video-after-restart")

    assert json.loads(request_bodies[0])["video_url"] == "https://www.douyin.com/video/video-after-restart"


@pytest.mark.asyncio
async def test_new_search_clears_previous_comment_cache_for_scheduled_collection():
    comment_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal comment_requests
        if request.url.path == "/api/collect/search":
            return httpx.Response(200, json={"task_id": f"task-{comment_requests + 1}"})
        if request.url.path.startswith("/api/collect/status/"):
            task_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "task_id": task_id,
                    "status": "completed",
                    "data": [{"video_id": "video-1", "video_url": "https://www.douyin.com/video/1", "title": "真实视频"}],
                },
            )
        if request.url.path == "/api/video/comments":
            comment_requests += 1
            return httpx.Response(
                200,
                json={
                    "coverage_status": "partial",
                    "comments": [{"comment_id": f"comment-{comment_requests}", "content": f"第 {comment_requests} 次采集"}],
                },
            )
        raise AssertionError(f"unexpected endpoint: {request.url}")

    provider = DouyinCommentsCrawlerExternalProvider(
        "http://crawler.test",
        transport=httpx.MockTransport(handler),
    )

    first_videos = await provider.search_videos("长沙装修", 1)
    first = await provider.get_comments(first_videos[0].video_id)
    second_videos = await provider.search_videos("长沙装修", 1)
    second = await provider.get_comments(second_videos[0].video_id)

    assert comment_requests == 2
    assert first.items[0].content == "第 1 次采集"
    assert second.items[0].content == "第 2 次采集"
