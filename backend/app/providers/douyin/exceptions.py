"""Explicit failures for the real Douyin browser provider.

Callers can safely map ``code`` to an API error without mistaking an empty
collection for a successful scrape.
"""

from __future__ import annotations

from typing import Any


class DouyinError(RuntimeError):
    code = "DOUYIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class DouyinBrowserError(DouyinError):
    code = "DOUYIN_BROWSER_ERROR"


class DouyinLoginRequired(DouyinError):
    code = "DOUYIN_LOGIN_REQUIRED"


class DouyinVerificationRequired(DouyinError):
    code = "DOUYIN_VERIFICATION_REQUIRED"


class DouyinLoginExpired(DouyinError):
    code = "DOUYIN_LOGIN_EXPIRED"


class DouyinPageLoadError(DouyinError):
    code = "DOUYIN_PAGE_LOAD_FAILED"


class DouyinPageParseError(DouyinError):
    code = "DOUYIN_PAGE_PARSE_FAILED"


class DouyinSelectorNotFound(DouyinError):
    code = "DOUYIN_SELECTOR_NOT_FOUND"

    def __init__(
        self,
        selector_name: str,
        *,
        candidates: list[str] | tuple[str, ...] = (),
        url: str = "",
        title: str = "",
        dom_summary: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "selector_name": selector_name,
            "candidates": list(candidates),
            "url": url,
            "title": title,
            "dom_summary": dom_summary,
        }
        if detail:
            merged.update(detail)
        super().__init__(
            f"Douyin selector not found: {selector_name}",
            detail=merged,
        )
        self.selector_name = selector_name


class DouyinCommentNotFound(DouyinError):
    code = "DOUYIN_COMMENT_NOT_FOUND"


class DouyinCommentAmbiguous(DouyinError):
    code = "DOUYIN_COMMENT_AMBIGUOUS"


class DouyinReplyFailed(DouyinError):
    code = "DOUYIN_REPLY_FAILED"


class DouyinReplyNotVerified(DouyinError):
    code = "DOUYIN_REPLY_NOT_VERIFIED"
