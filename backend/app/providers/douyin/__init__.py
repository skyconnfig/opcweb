"""Real browser-backed Douyin content provider.

This package intentionally uses only Playwright DOM/ARIA/text APIs.  It does
not contain a visual model, stealth browser, CAPTCHA solver, or fingerprint
evasion code.
"""

from app.providers.douyin.dto import (
    DouyinCommentDTO,
    DouyinLoginStatus,
    DouyinVideoDTO,
    LoginStatus,
    ReplyResult,
    ReplyStatus,
)
from app.providers.douyin.browser_manager import DouyinBrowserManager
from app.providers.douyin.exceptions import (
    DouyinBrowserError,
    DouyinCommentAmbiguous,
    DouyinCommentNotFound,
    DouyinError,
    DouyinLoginExpired,
    DouyinLoginRequired,
    DouyinPageLoadError,
    DouyinPageParseError,
    DouyinReplyFailed,
    DouyinReplyNotVerified,
    DouyinSelectorNotFound,
    DouyinVerificationRequired,
)
from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider

__all__ = [
    "DouyinCommentDTO",
    "DouyinBrowserError",
    "DouyinBrowserManager",
    "DouyinCommentAmbiguous",
    "DouyinCommentNotFound",
    "DouyinError",
    "DouyinLoginExpired",
    "DouyinLoginRequired",
    "DouyinLoginStatus",
    "DouyinPageLoadError",
    "DouyinPageParseError",
    "DouyinPlaywrightProvider",
    "DouyinReplyFailed",
    "DouyinReplyNotVerified",
    "DouyinSelectorNotFound",
    "DouyinVideoDTO",
    "LoginStatus",
    "ReplyResult",
    "ReplyStatus",
]
