"""Centralized, multi-level selectors for Douyin's changing DOM.

The order is deliberate: stable data attributes first, then ARIA roles and
labels, then visible text, and finally conservative CSS fallbacks.  These are
selectors only; no screenshot or visual inference is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SelectorKind = Literal["css", "role", "label", "text"]


@dataclass(frozen=True)
class SelectorSpec:
    kind: SelectorKind
    value: str
    name: str = ""

    def describe(self) -> str:
        if self.kind == "role":
            return f"role={self.value!r}, name={self.name!r}"
        return f"{self.kind}={self.value!r}"


def css(value: str) -> SelectorSpec:
    return SelectorSpec("css", value)


def role(value: str, name: str) -> SelectorSpec:
    return SelectorSpec("role", value, name)


def label(value: str) -> SelectorSpec:
    return SelectorSpec("label", value)


def text(value: str) -> SelectorSpec:
    return SelectorSpec("text", value)


SELECTORS: dict[str, tuple[SelectorSpec, ...]] = {
    "login.button": (
        css('[data-e2e="nav-login"]'),
        css('[data-e2e="login-button"]'),
        role("button", "登录"),
        label("登录"),
        text("登录"),
    ),
    "login.qr": (
        css('[data-e2e*="qrcode"]'),
        css('[class*="qrcode" i]'),
        css('[aria-label*="二维码" i]'),
        text("扫码登录"),
        text("请扫码登录"),
    ),
    "login.account": (
        css('[data-e2e="user-avatar"]'),
        css('[data-e2e="nav-user"]'),
        css('[data-e2e="user-profile"]'),
        role("button", "个人主页"),
        role("button", "用户菜单"),
        css('[data-e2e*="nav-user" i] a[href*="/user/"]'),
    ),
    "verification.marker": (
        css('[data-e2e*="verify" i]'),
        css('[class*="captcha" i]'),
        text("验证码"),
        text("安全验证"),
        text("滑动验证"),
        text("验证身份"),
    ),
    "login.expired": (
        text("登录已过期"),
        text("登录失效"),
        text("请重新登录"),
        text("重新登录"),
    ),
    "login.panel": (
        css('[id^="login-full-panel"]'),
    ),
    "search.input": (
        css('[data-e2e="searchbar-input"]'),
        css('input[placeholder*="搜索"]'),
        role("textbox", "搜索"),
        label("搜索"),
    ),
    "search.video_results": (
        css('a[href*="/video/"]'),
        css('[data-e2e="search-result-card"] a[href]'),
        css('[data-e2e*="search-result"] a[href]'),
        css('li a[href*="/video/"]'),
    ),
    "video.title": (
        css('[data-e2e="video-title"]'),
        css('[data-e2e="video-desc"]'),
        css('h1'),
        css('h2'),
    ),
    "video.description": (
        css('[data-e2e="video-desc"]'),
        css('[data-e2e="video-description"]'),
        css('[class*="desc" i]'),
    ),
    "video.creator": (
        css('[data-e2e="video-author"]'),
        css('[data-e2e="author-name"]'),
        css('a[href*="/user/"]'),
    ),
    "video.cover": (
        css('[data-e2e="video-cover"] img'),
        css('video[poster]'),
        css('img[src]'),
    ),
    "comment.container": (
        # The current desktop Douyin comment scroller.  Keep this ahead of
        # generic class fallbacks because modal and standalone pages differ.
        css('.comment-mainContent'),
        css('[data-e2e="comment-list"]'),
        css('[data-e2e="comment-container"]'),
        css('[data-e2e="comment-list-container"]'),
        css('[class*="comment-list" i]'),
    ),
    "comment.item": (
        css('[data-e2e="comment-item"]'),
        css('[data-e2e*="comment-item"]'),
        css('[data-comment-id]'),
        css('li[class*="comment" i]'),
    ),
    "comment.open_button": (
        css('[data-e2e="feed-comment-icon"]'),
        role("button", "评论"),
        label("评论"),
    ),
    "comment.anchor": (
        css('[id^="tooltip_"]'),
    ),
    "comment.expand_replies": (
        css('[data-e2e*="expand" i][role="button"]'),
        css('[data-e2e*="sub-comment" i][role="button"]'),
        role("button", "展开回复"),
        role("button", "查看回复"),
        role("button", "更多回复"),
        role("button", "条回复"),
        text("展开回复"),
        text("查看回复"),
        text("更多回复"),
        text("条回复"),
    ),
    "comment.load_more": (
        css('[data-e2e*="comment-more" i]'),
        role("button", "加载更多评论"),
        role("button", "查看更多评论"),
        text("加载更多评论"),
        text("查看更多评论"),
    ),
    "comment.content": (
        css('[data-e2e="comment-content"]'),
        css('[data-e2e="comment-text"]'),
        # Current desktop standalone pages render the actual text in this
        # class; the author remains in a separate /user/ anchor above it.
        css('[class~="FduGc_lz"]'),
        css('[class*="comment-content" i]'),
    ),
    "comment.author": (
        css('[data-e2e="comment-user-name"]'),
        css('[data-e2e="comment-author"]'),
        css('a[href*="/user/"]'),
    ),
    "comment.profile": (
        css('a[href*="/user/"]'),
        css('[data-e2e="comment-user-avatar"] a[href]'),
    ),
    "comment.link": (
        css('a[href*="/comment/"]'),
        css('[data-comment-id] a[href]'),
    ),
    "comment.reply_button": (
        # Observed on the current modal comment layout.  It is intentionally
        # followed by semantic fallbacks because this class may be rotated.
        css('.LpRGQ4Gi'),
        css('[data-e2e="comment-reply"]'),
        role("button", "回复"),
        label("回复"),
        text("回复"),
    ),
    "comment.reply_input": (
        css('.public-DraftEditor-content[contenteditable="true"]'),
        css('.DraftEditor-root [contenteditable="true"]'),
        css('[data-contents="true"] [contenteditable="true"]'),
        css('textarea[placeholder*="回复"]'),
        css('[contenteditable="true"]'),
        role("textbox", "回复"),
        css('textarea'),
    ),
    "comment.send_button": (
        css('.f5hSYimo.siaMKBB_'),
        css('[data-e2e="comment-submit"]'),
        role("button", "发送"),
        label("发送"),
        text("发送"),
    ),
    "comment.reply_target": (
        css('.comment-input-container .PPsJmqBy'),
        css('[id^="placeholder-"]'),
    ),
    "overlay.login_dialog": (
        css('.trust-login-dialog-button-cancel'),
        css('.trust-login-dialog-button-confirm'),
    ),
    "overlay.guide_mask": (
        css('#douyin-web-recommend-guide-mask button'),
        css('[data-e2e="recommend-guide-mask"] button'),
        css('#douyin-web-recommend-guide-mask [role="button"]'),
        css('[data-e2e="recommend-guide-mask"] [role="button"]'),
    ),
}


def selector_descriptions(name: str) -> list[str]:
    return [spec.describe() for spec in SELECTORS.get(name, ())]
