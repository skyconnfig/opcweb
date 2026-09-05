"""Manual, DOM-only smoke test with an optional redacted evidence report."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.providers.douyin.dto import LoginStatus
from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_report(path: str | None, report: dict) -> None:
    if not path:
        return
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={target}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", required=True, help="要在抖音搜索的真实文本关键词")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--wait-login", type=int, default=120, help="未登录时等待人工扫码的秒数")
    parser.add_argument("--report", help="可选的脱敏 JSON 验收报告路径")
    args = parser.parse_args()

    started_at = utc_now()
    settings = get_settings()
    provider = DouyinPlaywrightProvider(
        profile_dir=settings.douyin_profile_dir,
        browser_channel=settings.douyin_browser_channel,
        headless=False,
    )
    report: dict = {
        "run_id": f"douyin-playwright-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "operator": "local-user",
        "started_at": started_at,
        "finished_at": None,
        "timezone": "UTC",
        "environment": "Windows local persistent Playwright Chromium",
        "provider": provider.name,
        "profile_path": str(provider.browser.profile_dir),
        "keyword": args.keyword,
        "login_state": None,
        "verification_required": False,
        "result_count": 0,
        "videos": [],
        "reply_sent": False,
        "credentials_or_cookies_recorded": False,
        "acceptance": False,
        "failure": None,
    }

    try:
        await provider.start_browser()
        report["browser_started_at"] = utc_now()
        status = await provider.get_login_status()
        report["login_state"] = status.value
        report["verification_required"] = status is LoginStatus.VERIFICATION_REQUIRED
        print(f"login={status.value}")
        if status is not LoginStatus.LOGGED_IN:
            print("请在已打开的真实抖音浏览器中人工扫码/登录；不会自动处理验证码。")
            for _ in range(args.wait_login):
                await asyncio.sleep(1)
                status = await provider.get_login_status()
                report["login_state"] = status.value
                report["verification_required"] = status is LoginStatus.VERIFICATION_REQUIRED
                if status is LoginStatus.LOGGED_IN:
                    break
            if status is not LoginStatus.LOGGED_IN:
                raise RuntimeError(f"登录未确认：{status.value}")
        report["login_verified_at"] = utc_now()

        report["search_started_at"] = utc_now()
        videos = await provider.search_videos(args.keyword, args.limit)
        report["search_completed_at"] = utc_now()
        report["result_count"] = len(videos)
        report["result_count_dom"] = len(videos)
        report["search_result_selector"] = provider.last_dom_trace.get("search_result_selector", "")
        print(f"videos={len(videos)}")
        for video in videos:
            print(f"video id={video.video_id} url={video.url} title={video.title}")
            opened_at = utc_now()
            comments = await provider.get_comments(video.video_id)
            fetched_at = utc_now()
            trace = dict(provider.last_dom_trace)
            print(f"comments={comments.items_received} coverage={comments.coverage_status}")
            samples = []
            for comment in comments.items[:10]:
                print(
                    f"  comment id={comment.comment_id} "
                    f"id_source={getattr(comment, 'id_source', 'unknown')} "
                    f"user={comment.nickname} text={comment.content}"
                )
                samples.append(
                    {
                        "comment_id": comment.comment_id,
                        "id_source": getattr(comment, "id_source", "unknown"),
                        "parent_comment_id": comment.parent_comment_id or None,
                        "text": comment.content,
                    }
                )
            report["videos"].append(
                {
                    "video_id": video.video_id,
                    "url": video.url,
                    "title": video.title,
                    "description_present": bool(video.description),
                    "creator": video.creator,
                    "publish_time": video.publish_time.isoformat() if video.publish_time else None,
                    "like_count": video.likes,
                    "comment_count": video.comments,
                    "share_count": video.shares,
                    "collect_count": video.collects,
                    "page_shape": trace.get("page_shape", ""),
                    "comment_open_action": trace.get("comment_open_action", ""),
                    "comment_container_selector": trace.get("comment.container", ""),
                    "comments_received": comments.items_received,
                    "coverage": comments.coverage_status,
                    "comment_item_selector": trace.get("comment.item", ""),
                    "opened_at": opened_at,
                    "fetched_at": fetched_at,
                    "page_url": trace.get("page_url", video.url),
                    "samples": samples,
                }
            )

        report["acceptance"] = bool(
            report["login_state"] == LoginStatus.LOGGED_IN.value
            and report["videos"]
            and report.get("result_count_dom", 0) > 0
            and report.get("search_result_selector")
            and any(video["samples"] for video in report["videos"])
            and all(
                video.get("page_shape")
                and video.get("comment_open_action")
                and video.get("comment_item_selector")
                for video in report["videos"]
            )
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
        write_report(args.report, report)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
