"""Explicit manual reply test. Without --confirm it is always a dry run."""

from __future__ import annotations

import argparse
import asyncio

from app.db import SessionLocal
from app.models import Comment, Video
from app.core.config import get_settings
from app.providers.douyin.dto import DouyinCommentDTO
from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comment-id", required=True, type=int, help="系统 comments.id，不是猜测的抖音 ID")
    parser.add_argument("--text", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        print(f"DRY RUN: comment={args.comment_id} reply={args.text!r}; no browser action was performed")
        return
    with SessionLocal() as db:
        comment = db.get(Comment, args.comment_id)
        if not comment:
            raise SystemExit("评论不存在")
        video = db.get(Video, comment.video_id)
        if not video:
            raise SystemExit("评论对应的视频不存在")
        print(f"CONFIRMED: comment={comment.id} nickname={comment.nickname} content={comment.content!r} reply={args.text!r}")
        target = DouyinCommentDTO(platform=comment.platform, comment_id=comment.platform_comment_id, user_id=comment.platform_user_id, nickname=comment.nickname, profile_url=comment.profile_url, content=comment.content, created_at=comment.created_at_platform, parent_comment_id=comment.parent_comment_id, id_source=comment.id_source)
        settings = get_settings()
        provider = DouyinPlaywrightProvider(profile_dir=settings.douyin_profile_dir, browser_channel=settings.douyin_browser_channel, headless=False)
        try:
            await provider.start_browser()
            result = await provider.reply_comment(video.url, target, args.text)
            print(f"status={result.status.value} verified={result.verified}")
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
