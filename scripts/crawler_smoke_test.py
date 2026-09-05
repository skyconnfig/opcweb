"""Real crawler smoke test with an optional redacted evidence report.

This script never creates fixture data and never sends replies. It proves the
external provider's actual chain: keyword -> collected video -> video URL ->
comments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.providers.external.douyin_comments_crawler import DouyinCommentsCrawlerExternalProvider


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_report(path: str | None, report: dict) -> None:
    if not path:
        return
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={target}")


async def run(base_url: str, keyword: str, limit: int, report_path: str | None = None) -> None:
    provider = DouyinCommentsCrawlerExternalProvider(base_url, timeout=60)
    report: dict = {
        "run_id": f"douyin-crawler-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "operator": "local-user",
        "started_at": utc_now(),
        "finished_at": None,
        "timezone": "UTC",
        "environment": "Windows local crawler HTTP service",
        "provider": provider.name,
        "base_url": base_url,
        "keyword": keyword,
        "health_status": None,
        "videos": [],
        "credentials_or_cookies_recorded": False,
        "acceptance": False,
        "failure": None,
    }
    try:
        health = await provider.health_check()
        report["health_status"] = health.status
        print(f"crawler_health={health.status} message={health.message}")
        if health.status != "connected":
            raise RuntimeError("外部 douyin-comments-crawler 不可用；未生成替代数据。")

        videos = await provider.search_videos(keyword, limit)
        report["search_trace"] = dict(provider.last_collection_trace)
        print(f"videos={len(videos)}")
        if not videos:
            raise RuntimeError("关键词搜索未返回真实视频。")

        for video in videos:
            print(f"video id={video.video_id} url={video.url} title={video.title}")
            page = await provider.get_comments(video.video_id)
            print(f"comments={page.items_received} coverage={page.coverage_status}")
            samples = []
            for comment in page.items[:10]:
                print(f"  comment id={comment.comment_id} user={comment.nickname} text={comment.content}")
                samples.append(
                    {
                        "comment_id": comment.comment_id,
                        "parent_comment_id": comment.parent_comment_id or None,
                        "text": comment.content,
                        "source": "crawler_http",
                    }
                )
            report["videos"].append(
                {
                    "video_id": video.video_id,
                    "url": video.url,
                    "title": video.title,
                    "description_present": bool(video.description),
                    "author": video.creator,
                    "publish_time": video.publish_time.isoformat() if video.publish_time else None,
                    "like_count": video.likes,
                    "comment_count": video.comments,
                    "share_count": video.shares,
                    "collect_count": video.collects,
                    "comments_received": page.items_received,
                    "coverage": page.coverage_status,
                    "samples": samples,
                }
            )

        report["acceptance"] = bool(
            report["videos"]
            and any(video["samples"] for video in report["videos"])
            and all(
                sample.get("comment_id") and sample.get("text")
                for video in report["videos"]
                for sample in video["samples"]
            )
        )
        if not report["acceptance"]:
            raise RuntimeError("未取得同时具备真实视频 URL 和真实评论 ID/文本的完整结果")
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["finished_at"] = utc_now()
        write_report(report_path, report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--report", help="可选的脱敏 JSON 验收报告路径")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.keyword, args.limit, args.report))


if __name__ == "__main__":
    main()
