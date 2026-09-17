"""Run a real browser smoke test for the local Next.js console.

This checks the rendered application and its static assets; it does not create,
scan, reply to, or otherwise mutate product data.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


VIEWS = [
    "总览",
    "智能截流",
    "关键词雷达",
    "热门视频",
    "评论池",
    "潜客池",
    "AI 回复",
    "知识库",
    "人设配置",
    "智能体",
    "任务中心",
    "数据分析",
    "数据源",
    "抖音账号",
    "系统设置",
]

VIEW_ROUTES = [
    ("overview", "总览"),
    ("smart", "智能截流"),
    ("keywords", "关键词雷达"),
    ("videos", "热门视频"),
    ("comments", "评论池"),
    ("leads", "潜客池"),
    ("replies", "AI 回复"),
    ("knowledge", "知识库"),
    ("persona", "人设配置"),
    ("agents", "智能体"),
    ("tasks", "任务中心"),
    ("analytics", "数据分析"),
    ("providers", "数据源"),
    ("douyin", "抖音账号"),
    ("settings", "系统设置"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def run(base_url: str) -> dict[str, object]:
    console_errors: list[str] = []
    request_failures: list[str] = []
    visited_views: list[str] = []
    hash_views: list[str] = []
    interaction_checks: dict[str, bool | str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: request_failures.append(request.url))
        page.goto(base_url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)

        shell_display = page.locator(".product-shell").evaluate("element => getComputedStyle(element).display")
        sidebar_width = page.locator(".sidebar").evaluate("element => getComputedStyle(element).width")
        initial_loading_count = page.locator(".page-loading").count()

        if shell_display != "flex":
            raise AssertionError(f"product shell is not styled: display={shell_display}")
        if initial_loading_count:
            raise AssertionError("initial page is still loading after the local API settled")

        for view in VIEWS:
            if view != "总览":
                page.get_by_role("button", name=view, exact=True).click()
                page.wait_for_timeout(400)
                if page.locator(".page-loading").count():
                    page.wait_for_timeout(2_000)
            if not page.locator(".page").count():
                raise AssertionError(f"view did not render: {view}")
            visited_views.append(view)

        # Exercise deep links and same-document hash changes separately from
        # sidebar clicks. This catches a stale view when the browser restores
        # a bookmarked hash or when history navigation changes only the URL
        # fragment.
        for route, label in VIEW_ROUTES:
            page.goto(f"{base_url}#{route}", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(350)
            current_label = page.locator(".top-current").inner_text()
            if current_label != label:
                raise AssertionError(f"hash route did not render: #{route} -> {current_label!r}, expected {label!r}")
            hash_views.append(route)

        # Open each read-only detail surface when the current workspace has a
        # real row. Empty workspaces are valid and are reported as skipped,
        # rather than being filled with fixtures just to satisfy the smoke.
        page.goto(f"{base_url}#comments", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)
        comment_rows = page.locator(".comments-data tbody tr")
        if comment_rows.count():
            comment_rows.first.locator(".table-link").first.click()
            page.wait_for_timeout(350)
            interaction_checks["comment_dialog"] = page.locator('[role="dialog"][aria-label="评论详情"]').count() == 1
            page.locator('[role="dialog"][aria-label="评论详情"] button[aria-label="关闭评论详情"]').click()
        else:
            interaction_checks["comment_dialog"] = "skipped_empty"

        page.goto(f"{base_url}#leads", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)
        lead_rows = page.locator(".lead-data tbody tr")
        if lead_rows.count():
            lead_rows.first.click()
            page.wait_for_timeout(350)
            interaction_checks["lead_dialog"] = page.locator('[role="dialog"][aria-label="潜客详情"]').count() == 1
            interaction_checks["follow_up_tasks"] = page.get_by_text("FOLLOW-UP TASKS", exact=True).count() == 1
            page.locator('[role="dialog"][aria-label="潜客详情"] button[aria-label="关闭潜客详情"]').click()
        else:
            interaction_checks["lead_dialog"] = "skipped_empty"
            interaction_checks["follow_up_tasks"] = "skipped_empty"

        page.goto(f"{base_url}#videos", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)
        video_rows = page.locator(".video-row")
        if video_rows.count():
            video_rows.first.click()
            page.wait_for_timeout(350)
            interaction_checks["video_dialog"] = page.locator('[role="dialog"][aria-label="视频详情"]').count() == 1
            page.locator('[role="dialog"][aria-label="视频详情"] button[aria-label="关闭视频详情"]').click()
        else:
            interaction_checks["video_dialog"] = "skipped_empty"

        page.goto(f"{base_url}#tasks", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)
        task_rows = page.locator(".task-item")
        if task_rows.count():
            task_rows.first.click()
            page.wait_for_timeout(350)
            interaction_checks["task_detail"] = page.get_by_text("TASK DETAIL", exact=True).count() == 1
        else:
            interaction_checks["task_detail"] = "skipped_empty"

        interaction_checks["follow_up_center"] = page.get_by_text("FOLLOW-UP CENTER", exact=True).count() == 1

        page.goto(f"{base_url}#settings", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(700)
        interaction_checks["text_model_settings"] = page.get_by_text("文本模型配置", exact=True).count() == 1

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile_failures: list[str] = []
        mobile.on("requestfailed", lambda request: mobile_failures.append(request.url))
        mobile.goto(base_url, wait_until="networkidle", timeout=30_000)
        mobile.wait_for_timeout(700)
        mobile.get_by_role("button", name="打开导航", exact=True).click()
        mobile_open = mobile.locator(".sidebar.mobile-open").count() == 1
        mobile.get_by_role("button", name="关闭导航", exact=True).click()
        mobile_closed = mobile.locator(".sidebar.mobile-open").count() == 0
        no_horizontal_overflow = mobile.evaluate("document.body.scrollWidth <= window.innerWidth")
        browser.close()

    expected_stream_disconnects = [url for url in request_failures if "/api/events/stream" in url]
    unexpected_request_failures = [url for url in request_failures if "/api/events/stream" not in url]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "desktop": {
            "shell_display": shell_display,
            "sidebar_width": sidebar_width,
            "initial_loading_count": initial_loading_count,
            "visited_views": visited_views,
            "hash_views": hash_views,
            "interaction_checks": interaction_checks,
            "console_errors": console_errors,
            "expected_stream_disconnects": expected_stream_disconnects,
            "unexpected_request_failures": unexpected_request_failures,
        },
        "mobile": {
            "menu_open": mobile_open,
            "menu_close": mobile_closed,
            "no_horizontal_overflow": no_horizontal_overflow,
            "request_failures": mobile_failures,
        },
        "passed": bool(
            len(visited_views) == len(VIEWS)
            and len(hash_views) == len(VIEW_ROUTES)
            and all(value is True or value == "skipped_empty" for value in interaction_checks.values())
            and not console_errors
            and not unexpected_request_failures
            and mobile_open
            and mobile_closed
            and no_horizontal_overflow
            and not mobile_failures
        ),
    }


def main() -> int:
    args = parse_args()
    try:
        report = run(args.base_url.rstrip("/"))
    except Exception as error:  # pragma: no cover - CLI failure is reported as JSON
        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "passed": False,
            "error": str(error),
        }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
