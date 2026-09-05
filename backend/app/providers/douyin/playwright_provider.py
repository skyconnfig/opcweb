"""Real, DOM-only Playwright provider for Douyin.

The provider is intentionally conservative.  It raises an explicit
``DouyinError`` when a page cannot be read, rather than turning a selector
change, login wall, or verification page into an empty successful result.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, unquote, urljoin, urlparse

from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth
from app.providers.douyin.browser_manager import DouyinBrowserManager
from app.providers.douyin.dto import (
    DouyinCommentDTO,
    DouyinVideoDTO,
    LoginStatus,
    ReplyResult,
    ReplyStatus,
)
from app.providers.douyin.exceptions import (
    DouyinCommentAmbiguous,
    DouyinCommentNotFound,
    DouyinError,
    DouyinLoginExpired,
    DouyinLoginRequired,
    DouyinPageLoadError,
    DouyinPageParseError,
    DouyinReplyFailed,
    DouyinSelectorNotFound,
    DouyinVerificationRequired,
)
from app.providers.douyin.selectors import SELECTORS, SelectorSpec, selector_descriptions


class DouyinPlaywrightProvider(BaseContentProvider):
    """Persistent, visible-browser implementation of the content provider."""

    name = "Douyin Playwright"
    platform = "douyin"
    comment_page_size = 50
    capabilities = {
        "keyword_search": True,
        "video_detail": True,
        "comments": True,
        # Reply expansion is page-version dependent.  Until we can observe a
        # stable parent_comment_id association in the live DOM, do not claim
        # this capability to callers.
        "sub_comments": False,
        "creator": True,
        "reply_comment": True,
        "login": True,
    }
    home_url = DouyinBrowserManager.HOME_URL
    session_cookie_names = frozenset({"sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss"})

    def __init__(
        self,
        browser_manager: DouyinBrowserManager | None = None,
        *,
        profile_dir: str | None = None,
        browser_channel: str | None = None,
        headless: bool = False,
        proxy_server: str | None = None,
    ) -> None:
        self.browser = browser_manager or DouyinBrowserManager(
            profile_dir=profile_dir,
            channel=browser_channel,
            headless=headless,
            proxy_server=proxy_server,
        )
        self._video_urls: dict[str, str] = {}
        self.last_dom_trace: dict[str, Any] = {}

    async def start_browser(self) -> None:
        await self.browser.start(url=self.home_url)

    async def close_browser(self) -> None:
        await self.browser.close()

    async def start(self) -> None:
        await self.start_browser()

    async def close(self) -> None:
        await self.close_browser()

    async def ensure_browser_started(self) -> None:
        """Restore the persistent browser before a real data action.

        A backend restart disposes the Playwright context, but it does not
        invalidate the on-disk Chromium profile.  Starting lazily here lets
        the saved session be reused for search/comments/replies instead of
        turning every restart into a new login flow.
        """

        is_running = getattr(self.browser, "is_running", True)
        is_healthy = getattr(self.browser, "is_healthy", None)
        if callable(is_healthy):
            is_running = is_running and await is_healthy()
        if not is_running:
            # A stale Playwright context cannot be reused.  ``close`` is
            # intentionally best-effort and keeps the on-disk Profile intact.
            await self.browser.close()
            await self.start_browser()

    async def get_login_status(self) -> LoginStatus:
        if not self.browser.is_running:
            return LoginStatus.ERROR
        try:
            async with self.browser.locked_page() as page:
                return await self._detect_login_status(page)
        except DouyinError:
            return LoginStatus.ERROR
        except Exception:
            return LoginStatus.ERROR

    async def login_status(self) -> LoginStatus:
        return await self.get_login_status()

    async def health_check(self) -> ProviderHealth:
        """Check the real browser, page reachability, and login state."""

        try:
            await self.browser.start(url=self.home_url)
            status = await self.get_login_status()
            if status is LoginStatus.LOGGED_IN:
                return ProviderHealth("connected", "browser=running login=logged_in")
            if status in {LoginStatus.LOGGED_OUT, LoginStatus.WAITING_LOGIN}:
                return ProviderHealth("login_required", f"browser=running login={status.value}")
            if status is LoginStatus.VERIFICATION_REQUIRED:
                return ProviderHealth("verification_required", "browser=running login=verification_required")
            if status is LoginStatus.EXPIRED:
                return ProviderHealth("login_expired", "browser=running login=expired")
            return ProviderHealth("unavailable", "无法从真实页面确定抖音登录状态")
        except DouyinError as exc:
            return ProviderHealth("unavailable", f"{exc.code}: {exc.message}")

    async def search_videos(self, keyword: str, limit: int) -> list[DouyinVideoDTO]:
        keyword = keyword.strip()
        if not keyword:
            raise DouyinPageParseError("搜索关键词不能为空")
        if limit < 1:
            raise DouyinPageParseError("搜索条数必须大于 0")

        await self.ensure_browser_started()
        async with self.browser.locked_page() as page:
            await self._require_login(page)
            search_url = f"{self.home_url.rstrip('/')}/search/{quote(keyword, safe='')}?type=video"
            await self._navigate(page, search_url, action="search_videos")
            self.last_dom_trace["page_shape"] = "search_results_dom"
            self.last_dom_trace["page_url"] = str(getattr(page, "url", search_url))
            # A logged-in session can still be stopped by Douyin's security
            # interstitial after navigation. Re-check the destination page so
            # callers get an actionable verification error instead of a
            # misleading missing-selector error.
            await self._require_login(page)
            search_input = await self._find(page, "search.input", page=page)
            try:
                await search_input.fill(keyword)
                await search_input.press("Enter")
                search_state = await self._wait_for_search_surface(page)
            except Exception as exc:
                await self.browser.capture_debug(page, action="search_videos", selector="search.input", error=str(exc))
                raise DouyinPageParseError(
                    "无法在真实抖音页面提交搜索关键词",
                    detail={"keyword": keyword, "error": str(exc)},
                ) from exc

            self.last_dom_trace["search_surface"] = search_state
            if search_state == "timeout":
                # A committed search shell can occasionally miss the client
                # result request. Retry the same real text search once before
                # reporting selector drift. This remains bounded and reads
                # only the real page DOM.
                try:
                    await self._navigate(page, search_url, action="search_videos_retry")
                    await self._require_login(page)
                    search_input = await self._find(page, "search.input", page=page)
                    await search_input.fill(keyword)
                    await search_input.press("Enter")
                    search_state = await self._wait_for_search_surface(page)
                    self.last_dom_trace["search_surface_retry"] = search_state
                except Exception as exc:
                    await self.browser.capture_debug(page, action="search_videos_retry", selector="search.input", error=str(exc))
                    raise DouyinPageParseError(
                        "无法在真实抖音页面恢复搜索结果",
                        detail={"keyword": keyword, "error": str(exc)},
                    ) from exc

            if search_state == "empty":
                return []
            if search_state != "results":
                await self.browser.capture_debug(page, action="search_videos", selector="search.video_results", error="search surface timeout")
                raise DouyinPageParseError(
                    "真实抖音搜索结果未在限定时间内加载",
                    detail={"keyword": keyword, "selector": "search.video_results"},
                )

            links = await self._find_all(page, "search.video_results", page=page)
            self.last_dom_trace["search_result_selector"] = self.last_dom_trace.get("search.video_results", "")
            await self._scroll_until_stable(page, links, rounds=5)
            videos: list[DouyinVideoDTO] = []
            seen: set[str] = set()
            for index in range(await links.count()):
                if len(videos) >= limit:
                    break
                link = links.nth(index)
                href = await self._attribute(link, "href")
                video_id = _video_id_from_url(href)
                if not href or not video_id or video_id in seen:
                    continue
                card = await _nearest_result_container(link)
                card_text = await _inner_text(card or link)
                title = await self._optional_text(link, page=page)
                title = title or await self._optional_text(card, "video.title", page=page)
                if not title:
                    title = _first_meaningful_line(card_text)
                if not title:
                    continue
                description = await self._optional_text(card, "video.description", page=page)
                creator = await self._optional_text(card, "video.creator", page=page)
                cover = await self._optional_media_url(card, page=page)
                video_url = urljoin(self.home_url, href)
                video = DouyinVideoDTO(
                    platform="douyin",
                    video_id=video_id,
                    title=title,
                    description=description,
                    creator=creator,
                    url=video_url,
                    cover=cover,
                    publish_time=_parse_datetime(card_text),
                    likes=_metric(card_text, ("赞", "点赞")),
                    comments=_metric(card_text, ("评论",)),
                    shares=_metric(card_text, ("分享",)),
                    collects=_metric(card_text, ("收藏",)),
                    keyword=keyword,
                )
                videos.append(video)
                seen.add(video_id)
                self._video_urls[video_id] = video_url

            if not videos:
                await self.browser.capture_debug(page, action="search_videos", selector="search.video_results", error="no parseable video result")
                raise DouyinPageParseError(
                    "真实抖音搜索结果中没有可解析的视频",
                    detail={"keyword": keyword, "selector": "search.video_results"},
                )
            return videos

    async def get_video(self, video_id: str) -> DouyinVideoDTO | None:
        video_url = self._video_url(video_id)
        await self.ensure_browser_started()
        async with self.browser.locked_page() as page:
            await self._require_login(page)
            await self._navigate_if_needed(page, video_url, action="get_video")
            await self._require_login(page)
            title = await self._optional_text(page, "video.title", page=page)
            description = await self._optional_text(page, "video.description", page=page)
            creator = await self._optional_text(page, "video.creator", page=page)
            cover = await self._optional_media_url(page, page=page)
            body = await _body_text(page)
            if not title:
                await self.browser.capture_debug(page, action="get_video", selector="video.title", error="title missing")
                raise DouyinSelectorNotFound(
                    "video.title",
                    candidates=selector_descriptions("video.title"),
                    url=str(getattr(page, "url", "")),
                )
            result = DouyinVideoDTO(
                platform="douyin",
                video_id=_video_id_from_url(video_url) or video_id,
                title=title,
                description=description,
                creator=creator,
                url=video_url,
                cover=cover,
                publish_time=_parse_datetime(body),
                likes=_metric(body, ("赞", "点赞")),
                comments=_metric(body, ("评论",)),
                shares=_metric(body, ("分享",)),
                collects=_metric(body, ("收藏",)),
                keyword="",
            )
            self._video_urls[result.video_id] = video_url
            return result

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        offset = _parse_dom_cursor(cursor)
        video_url = self._video_url(video_id)
        await self.ensure_browser_started()
        async with self.browser.locked_page() as page:
            await self._require_login(page)
            await self._navigate_if_needed(page, video_url, action="get_comments")
            await self._require_login(page)
            container = await self._prepare_comment_surface(page, action="get_comments")
            self.last_dom_trace["page_url"] = str(getattr(page, "url", video_url))
            items = await self._wait_for_comment_items(container, page=page, allow_empty_state=True)
            if items is None:
                return CommentScanResult(
                    items=[],
                    # An explicit empty/closed state proves that no comments
                    # were rendered for this page, but it does not prove that
                    # the platform has no other public comments.
                    coverage_status="partial",
                    items_received=0,
                    next_cursor=None,
                    has_more=False,
                )
            await self._scroll_comments(page, container, items)
            await self._load_more_comments(page, container, items)
            await self._expand_reply_threads(page, items)
            # Locators are live, but re-resolving after expansion makes the
            # contract explicit and includes newly inserted reply nodes.
            items = await self._wait_for_comment_items(container, page=page)
            await self._scroll_comments(page, container, items)
            parsed: list[DouyinCommentDTO] = []
            seen: set[str] = set()
            for index in range(await items.count()):
                comment = await self._parse_comment(items.nth(index), video_url, page=page)
                if comment is None or comment.comment_id in seen:
                    continue
                parsed.append(comment)
                seen.add(comment.comment_id)
            if not parsed:
                await self.browser.capture_debug(page, action="get_comments", selector="comment.item", error="no parseable comments")
                raise DouyinPageParseError(
                    "真实抖音评论区域存在，但没有可解析的评论文本",
                    detail={"video_url": video_url, "cursor": cursor},
                )
            page_end = min(offset + self.comment_page_size, len(parsed))
            if offset > len(parsed):
                raise DouyinPageParseError(
                    "评论分页游标超出当前 DOM 范围",
                    detail={"cursor": cursor, "items_loaded": len(parsed), "video_url": video_url},
                )
            page_items = parsed[offset:page_end]
            has_more = page_end < len(parsed)
            return CommentScanResult(
                items=page_items,
                # A DOM scroll can only prove what was rendered in this
                # session. It cannot prove that the platform returned every
                # public comment, so never claim complete coverage here.
                coverage_status="partial",
                items_received=len(page_items),
                next_cursor=_dom_cursor(page_end) if has_more else None,
                has_more=has_more,
            )

    async def reply_comment(
        self,
        video_url: str,
        comment: CommentDTO,
        text: str,
    ) -> ReplyResult:
        text = text.strip()
        if not text:
            raise DouyinReplyFailed("回复内容不能为空")
        target_url = self._video_url(video_url)
        await self.ensure_browser_started()
        async with self.browser.locked_page() as page:
            await self._require_login(page)
            await self._navigate_if_needed(page, target_url, action="reply_comment")
            await self._require_login(page)
            container = await self._prepare_comment_surface(page, action="reply_comment")
            items = await self._wait_for_comment_items(container, page=page)
            target, matches = await self._locate_comment_with_scrolling(page, container, items, comment)
            if target is None:
                if matches > 1:
                    await self.browser.capture_debug(page, action="reply_comment", selector="comment.item", error="comment ambiguous")
                    raise DouyinCommentAmbiguous("目标评论匹配到多个 DOM 节点")
                await self.browser.capture_debug(page, action="reply_comment", selector="comment.item", error="comment not found")
                raise DouyinCommentNotFound(
                    "未能在真实页面定位目标评论",
                    detail={"comment_id": comment.comment_id, "nickname": comment.nickname},
                )
            before = await _inner_text(target)
            try:
                reply_button = await self._find(target, "comment.reply_button", page=page)
                await reply_button.click()
                await self._wait_for_reply_target(page)
                reply_input = await self._find(page, "comment.reply_input", page=page)
                await reply_input.click()
                # Douyin's current editor is Draft.js.  It is React-controlled
                # and can ignore Locator.fill(); keyboard input triggers the
                # same onChange path as a human user.
                await page.keyboard.type(text, delay=40)
                send_button = await self._find(target, "comment.send_button", page=page, required=False)
                send_button = send_button or await self._find(page, "comment.send_button", page=page)
                await send_button.click()
            except DouyinError:
                raise
            except Exception as exc:
                await self.browser.capture_debug(page, action="reply_comment", selector="comment.send_button", error=str(exc))
                raise DouyinReplyFailed(
                    "抖音回复操作失败",
                    detail={"comment_id": comment.comment_id, "error": str(exc)},
                ) from exc

            verified = await self._verify_reply(page, target, text, before)
            status = ReplyStatus.VERIFIED if verified else ReplyStatus.SENT_UNVERIFIED
            return ReplyResult(
                status=status,
                platform="douyin",
                video_url=target_url,
                comment_id=comment.comment_id,
                reply_text=text,
                verified=verified,
                detail={"verification": "dom_exact_text" if verified else "not_observed_in_dom"},
            )

    async def verify_reply(
        self,
        video_url: str,
        comment: CommentDTO,
        text: str,
    ) -> ReplyResult:
        """Re-check an earlier send against the current rendered comment DOM.

        This method never clicks, types, or sends anything.  It is intentionally
        separate from ``reply_comment`` so a transient network/UI failure after
        a click can be reconciled without risking a duplicate reply.
        """

        text = text.strip()
        if not text:
            raise DouyinReplyFailed("核验内容不能为空")
        target_url = self._video_url(video_url)
        await self.ensure_browser_started()
        async with self.browser.locked_page() as page:
            await self._require_login(page)
            await self._navigate_if_needed(page, target_url, action="verify_reply")
            await self._require_login(page)
            container = await self._prepare_comment_surface(page, action="verify_reply")
            items = await self._wait_for_comment_items(container, page=page)
            target, matches = await self._locate_comment_with_scrolling(page, container, items, comment)
            if target is None:
                if matches > 1:
                    raise DouyinCommentAmbiguous("核验目标评论匹配到多个 DOM 节点")
                raise DouyinCommentNotFound(
                    "未能在真实页面定位待核验的目标评论",
                    detail={"comment_id": comment.comment_id, "nickname": comment.nickname},
                )
            verified = await self._verify_reply(page, target, text, "")
            return ReplyResult(
                status=ReplyStatus.VERIFIED if verified else ReplyStatus.SENT_UNVERIFIED,
                platform="douyin",
                video_url=target_url,
                comment_id=comment.comment_id,
                reply_text=text,
                verified=verified,
                detail={"verification": "dom_exact_text" if verified else "not_observed_in_dom"},
            )

    async def _prepare_comment_surface(self, page: Any, *, action: str) -> Any:
        """Return the comment scroller after handling both page layouts.

        Standalone video pages often render comments immediately.  Feed/modal
        pages require the user-visible comment icon to be clicked first.
        Overlays are dismissed through their DOM controls so they cannot
        intercept the real interaction.
        """

        await self._dismiss_blocking_overlays(page)
        container = await self._find(page, "comment.container", page=page, required=False)
        items = await self._find(page, "comment.item", page=page, required=False)
        if not container and not items:
            # Standalone video pages can finish their client-side hydration
            # after domcontentloaded. Poll the DOM briefly so a slow detail
            # page is not reported as selector drift on the first request.
            for _ in range(20):
                await page.wait_for_timeout(500)
                container = await self._find(page, "comment.container", page=page, required=False)
                items = await self._find(page, "comment.item", page=page, required=False)
                if container or items:
                    break
        if container or items:
            self.last_dom_trace["page_shape"] = "video_detail_dom"
            self.last_dom_trace["comment_open_action"] = "existing_dom_surface"
            return container or page

        open_button = await self._find(page, "comment.open_button", page=page, required=False)
        if open_button is None:
            # Let the required selector path produce a diagnostic snapshot.
            return await self._find(page, "comment.container", page=page)
        try:
            await open_button.click()
            await page.wait_for_timeout(500)
        except Exception as exc:
            await self.browser.capture_debug(page, action=action, selector="comment.open_button", error=str(exc))
            raise DouyinPageParseError(
                "无法打开真实抖音评论区",
                detail={"selector_name": "comment.open_button", "error": str(exc)},
            ) from exc
        self.last_dom_trace["page_shape"] = "feed_modal_dom"
        self.last_dom_trace["comment_open_action"] = f"click:{self.last_dom_trace.get('comment.open_button', 'comment.open_button')}"
        container = await self._find(page, "comment.container", page=page, required=False)
        return container or page

    async def _dismiss_blocking_overlays(self, page: Any) -> None:
        # These are ordinary page overlays observed in the external DOM
        # research.  No screenshot or visual inference is involved.
        await self._click_optional(page, "overlay.login_dialog", page=page, max_clicks=1)
        await self._click_optional(page, "overlay.guide_mask", page=page, max_clicks=1)

        # A guide mask can exist without an actionable button.  Hiding only
        # this non-security onboarding layer is the documented DOM fallback;
        # verification/captcha elements are never hidden or bypassed.
        try:
            await page.locator(
                '#douyin-web-recommend-guide-mask, [data-e2e="recommend-guide-mask"]'
            ).evaluate_all("nodes => nodes.forEach(node => { node.style.display = 'none'; })")
        except Exception:
            pass

    async def _wait_for_comment_items(
        self,
        container: Any,
        *,
        page: Any,
        allow_empty_state: bool = False,
    ) -> Any:
        """Wait for hydrated comment nodes after the surface is mounted."""

        if allow_empty_state:
            empty_state = await self._comment_empty_state(container, page=page)
            if empty_state:
                self.last_dom_trace["comment.empty_state"] = empty_state
                return None

        try:
            return await self._find_all(container, "comment.item", page=page)
        except DouyinSelectorNotFound:
            pass
        for _ in range(20):
            if allow_empty_state:
                empty_state = await self._comment_empty_state(container, page=page)
                if empty_state:
                    self.last_dom_trace["comment.empty_state"] = empty_state
                    return None
            await page.wait_for_timeout(500)
            for spec in SELECTORS.get("comment.item", ()):
                try:
                    locator = _make_locator(container, spec)
                    if await locator.count():
                        self.last_dom_trace["comment.item"] = spec.describe()
                        return locator
                except Exception:
                    continue
        return await self._find_all(container, "comment.item", page=page)

    async def _comment_empty_state(self, container: Any, *, page: Any) -> str:
        """Return a visible text-only empty/closed comment state, if present.

        Douyin can mount the real comment surface without rendering any
        comment item.  That is a valid per-video result, not selector drift.
        This deliberately uses only DOM text; it never infers state from an
        image, video frame, OCR result, or visual model.
        """

        text = await _inner_text(container)
        if not text:
            try:
                text = await _body_text(page)
            except Exception:
                # Some provider contract doubles expose only the locators
                # needed for comment parsing.  An unavailable body fallback
                # means "not observed", never a parse failure.
                text = ""
        normalized = re.sub(r"\s+", "", text).lower()
        states = (
            ("暂无评论", "no_comments"),
            ("暂时没有评论", "no_comments"),
            ("还没有评论", "no_comments"),
            ("评论已关闭", "comments_closed"),
            ("评论区已关闭", "comments_closed"),
            ("作者已关闭评论", "comments_closed"),
            ("主播已关闭评论", "comments_closed"),
        )
        for phrase, state in states:
            if phrase in normalized:
                return state
        return ""

    async def _wait_for_reply_target(self, page: Any) -> None:
        """Verify that clicking reply established the correct reply context."""

        for _ in range(12):
            target = await self._find(page, "comment.reply_target", page=page, required=False)
            if target is not None:
                text = await _inner_text(target)
                if "回复@" in text or "回复 @" in text:
                    return
            await page.wait_for_timeout(200)
        await self.browser.capture_debug(
            page,
            action="reply_comment",
            selector="comment.reply_target",
            error="reply target was not established",
        )
        raise DouyinReplyFailed("未确认抖音回复目标，已停止发送")

    async def _detect_login_status(self, page: Any) -> LoginStatus:
        try:
            body = await _body_text(page)
            valid_cookies = await self.browser.valid_session_cookie_names(self.home_url)
            # The login dialog can remain mounted but hidden after a session
            # is restored.  Prefer visible account/session evidence before
            # interpreting that dialog as logged out.  In particular, do not
            # scan the entire body for "验证码": Douyin ships verification
            # scripts and hidden challenge text on ordinary logged-in pages.
            has_session = bool({"sessionid", "sessionid_ss"} & valid_cookies) and bool(
                {"sid_guard", "uid_tt", "uid_tt_ss"} & valid_cookies
            )
            if not body:
                await self.browser.capture_debug(
                    page,
                    action="login_status",
                    selector="login markers",
                    error="page body is empty",
                )
                return LoginStatus.LOGGED_IN if has_session else LoginStatus.ERROR
            # These selectors are visibility checked by _find(), so a hidden
            # verification container or script text cannot invalidate a
            # restored session.  A visible challenge still fails closed.
            verification = await self._find(page, "verification.marker", page=page, required=False)
            if verification:
                return LoginStatus.VERIFICATION_REQUIRED
            expired = await self._find(page, "login.expired", page=page, required=False)
            if expired:
                return LoginStatus.EXPIRED
            if await self._find(page, "login.account", page=page, required=False):
                return LoginStatus.LOGGED_IN
            if has_session:
                return LoginStatus.LOGGED_IN
            lowered = body.lower()
            if await self._find(page, "login.panel", page=page, required=False):
                return LoginStatus.LOGGED_OUT
            qr = await self._find(page, "login.qr", page=page, required=False)
            login = await self._find(page, "login.button", page=page, required=False)
            if qr or _contains_any(lowered, ("扫码登录", "请扫码登录", "扫一扫登录")):
                return LoginStatus.WAITING_LOGIN
            if login:
                return LoginStatus.LOGGED_OUT
            await self.browser.capture_debug(
                page,
                action="login_status",
                selector="login.account; login.qr; login.button; verification.marker",
                error="no known login state marker found",
            )
            return LoginStatus.ERROR
        except Exception as exc:
            await self.browser.capture_debug(
                page,
                action="login_status",
                selector="login markers",
                error=f"login detection failed: {type(exc).__name__}: {exc}",
            )
            return LoginStatus.ERROR

    async def _require_login(self, page: Any) -> None:
        status = await self._detect_login_status(page)
        if status in {LoginStatus.LOGGED_OUT, LoginStatus.WAITING_LOGIN}:
            raise DouyinLoginRequired("抖音账号尚未登录，请先在打开的浏览器中完成扫码登录")
        if status is LoginStatus.VERIFICATION_REQUIRED:
            raise DouyinVerificationRequired("抖音页面需要人工完成安全验证")
        if status is LoginStatus.EXPIRED:
            raise DouyinLoginExpired("抖音登录状态已过期，请重新登录")
        if status is LoginStatus.ERROR:
            raise DouyinPageLoadError("无法从真实页面确定抖音登录状态")

    async def _navigate(self, page: Any, url: str, *, action: str) -> None:
        try:
            # Douyin search/video pages can keep client resources pending for
            # a long time even after the real document has committed.  The
            # provider only needs a committed page before it performs its
            # explicit DOM checks; requiring every script to finish turns a
            # usable page into a false DOUYIN_PAGE_LOAD_FAILED.
            await page.goto(url, wait_until="commit", timeout=20_000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                # Selector waits below remain authoritative.  A slow
                # domcontentloaded event must not hide a page that already
                # committed and can be parsed from the DOM.
                pass
        except Exception as exc:
            await self.browser.capture_debug(page, action=action, selector=url, error=str(exc))
            raise DouyinPageLoadError(
                f"抖音页面加载失败: {url}",
                detail={"url": url, "action": action, "error": str(exc)},
            ) from exc

    async def _navigate_if_needed(self, page: Any, url: str, *, action: str) -> None:
        current = str(getattr(page, "url", ""))
        if current.rstrip("/") != url.rstrip("/"):
            await self._navigate(page, url, action=action)

    async def _find(
        self,
        root: Any,
        name: str,
        *,
        page: Any,
        required: bool = True,
    ) -> Any | None:
        specs = SELECTORS.get(name, ())
        for spec in specs:
            try:
                locator = _make_locator(root, spec)
                count = await locator.count()
                for index in range(count):
                    candidate = locator.nth(index)
                    if await _is_visible(candidate):
                        self.last_dom_trace[name] = spec.describe()
                        return candidate
            except Exception:
                continue
        if not required:
            return None
        url, title, dom_summary = await _page_info(page)
        await self.browser.capture_debug(page, action=name, selector="; ".join(selector_descriptions(name)), error="selector not found")
        raise DouyinSelectorNotFound(
            name,
            candidates=selector_descriptions(name),
            url=url,
            title=title,
            dom_summary=dom_summary,
        )

    async def _find_all(self, root: Any, name: str, *, page: Any) -> Any:
        for spec in SELECTORS.get(name, ()):
            try:
                locator = _make_locator(root, spec)
                if await locator.count():
                    self.last_dom_trace[name] = spec.describe()
                    return locator
            except Exception:
                continue
        url, title, dom_summary = await _page_info(page)
        await self.browser.capture_debug(page, action=name, selector="; ".join(selector_descriptions(name)), error="collection selector not found")
        raise DouyinSelectorNotFound(
            name,
            candidates=selector_descriptions(name),
            url=url,
            title=title,
            dom_summary=dom_summary,
        )

    async def _optional_text(self, root: Any, name: str = "", *, page: Any) -> str:
        if root is None:
            return ""
        try:
            locator = await self._find(root, name, page=page, required=False) if name else root
            return (await _inner_text(locator)).strip() if locator is not None else ""
        except Exception:
            return ""

    async def _optional_media_url(self, root: Any, *, page: Any) -> str:
        for name in ("video.cover",):
            try:
                locator = await self._find(root, name, page=page, required=False)
                if locator is None:
                    continue
                for attr in ("src", "data-src", "poster"):
                    value = await self._attribute(locator, attr)
                    if value:
                        return urljoin(self.home_url, value)
            except Exception:
                continue
        return ""

    async def _parse_comment(self, item: Any, video_url: str, *, page: Any) -> DouyinCommentDTO | None:
        content = await self._optional_text(item, "comment.content", page=page)
        raw_item_text = await _inner_text(item)
        content = content or _first_meaningful_line(raw_item_text)
        if not content:
            return None
        nickname = await self._optional_text(item, "comment.author", page=page)
        profile = await self._find(item, "comment.profile", page=page, required=False)
        profile_url = urljoin(self.home_url, await self._attribute(profile, "href")) if profile else ""
        user_id = ""
        for attr in ("data-user-id", "data-uid", "data-userid"):
            user_id = await self._attribute(item, attr)
            if user_id:
                break
        if not user_id and profile_url:
            user_id = _last_path_segment(profile_url)
        created_at = _parse_datetime(raw_item_text)
        for attr in ("data-create-time", "data-created-at", "data-timestamp"):
            candidate = await self._attribute(item, attr)
            if candidate:
                created_at = _parse_datetime(candidate) or created_at
                break
        dom_id = ""
        id_source = "unknown"
        for attr in ("data-comment-id", "data-cid", "data-e2e-comment-id", "data-id"):
            dom_id = await self._attribute(item, attr)
            if dom_id:
                id_source = "dom_attribute"
                break
        anchor = await self._find(item, "comment.anchor", page=page, required=False)
        anchor_id = await self._attribute(anchor, "id")
        if not dom_id and anchor_id.startswith("tooltip_"):
            dom_id = anchor_id.removeprefix("tooltip_")
            id_source = "dom_attribute"
        comment_url = ""
        comment_link = await self._find(item, "comment.link", page=page, required=False)
        if comment_link:
            href = await self._attribute(comment_link, "href")
            comment_url = urljoin(self.home_url, href) if href else ""
            if not dom_id and href:
                dom_id = _comment_id_from_url(href)
                if dom_id:
                    id_source = "url"
        parent_id = ""
        for attr in ("data-parent-comment-id", "data-reply-to", "data-parent-id"):
            parent_id = await self._attribute(item, attr)
            if parent_id:
                break
        class_name = (await self._attribute(item, "class")).lower()
        is_reply = bool(parent_id or "reply" in class_name)
        # When Douyin renders no platform identifier, retain the comment with
        # a deterministic fingerprint. This is explicitly marked so callers
        # never mistake it for a platform comment ID; duplicate fingerprints
        # still remain ambiguous and cannot be auto-replied to.
        if not dom_id:
            identity = "\x1f".join(
                (
                    "douyin",
                    _video_id_from_url(video_url) or video_url,
                    user_id or profile_url,
                    content,
                    created_at.isoformat() if created_at else "",
                )
            )
            dom_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            id_source = "fingerprint"
        comment_id = dom_id
        return DouyinCommentDTO(
            platform="douyin",
            comment_id=comment_id,
            user_id=user_id,
            nickname=nickname,
            profile_url=profile_url,
            content=content,
            created_at=created_at,
            parent_comment_id=parent_id,
            video_url=video_url,
            comment_url=comment_url,
            is_reply=is_reply,
            like_count=_metric(raw_item_text, ("赞", "点赞")),
            id_source=id_source,
        )

    async def _locate_comment(self, items: Any, target: CommentDTO) -> tuple[Any | None, int]:
        candidates: list[Any] = []
        for index in range(await items.count()):
            item = items.nth(index)
            if await self._comment_matches(item, target):
                candidates.append(item)
        if len(candidates) == 1:
            return candidates[0], 1
        return None, len(candidates)

    async def _locate_comment_with_scrolling(
        self,
        page: Any,
        container: Any,
        items: Any,
        target: CommentDTO,
    ) -> tuple[Any | None, int]:
        """Locate a comment by stable DOM id, loading lazy-rendered items."""

        found, matches = await self._locate_comment(items, target)
        if found is not None or matches > 1:
            return found, matches

        # crawl-douyin observed an id="tooltip_<comment_id>" anchor inside
        # each comment item.  Use it as a fast path before content matching.
        if re.fullmatch(r"[A-Za-z0-9_-]+", target.comment_id or ""):
            anchor = page.locator(f'[id="tooltip_{target.comment_id}"]')
            for _ in range(6):
                try:
                    if await anchor.count():
                        item = anchor.nth(0).locator(
                            "xpath=ancestor::*[@data-e2e='comment-item'][1]"
                        )
                        if await item.count():
                            return item, 1
                except Exception:
                    pass
                try:
                    await container.evaluate("node => { node.scrollTop += 900; }")
                except Exception:
                    await page.mouse.wheel(0, 900)
                await page.wait_for_timeout(350)
                items = await self._find_all(container, "comment.item", page=page)
                found, matches = await self._locate_comment(items, target)
                if found is not None or matches > 1:
                    return found, matches
        return None, 0

    async def _comment_matches(self, item: Any, target: CommentDTO) -> bool:
        for attr in ("data-comment-id", "data-cid", "data-e2e-comment-id", "data-id"):
            value = await self._attribute(item, attr)
            if value and value == target.comment_id:
                return True
        anchor = await self._find(item, "comment.anchor", page=item, required=False)
        anchor_id = await self._attribute(anchor, "id")
        if anchor_id == f"tooltip_{target.comment_id}":
            return True
        body = await _inner_text(item)
        if target.content and target.content not in body:
            return False
        if target.nickname:
            nickname = await self._optional_text(item, "comment.author", page=item)
            if nickname and nickname != target.nickname:
                return False
        if target.user_id:
            user_id = await self._attribute(item, "data-user-id") or await self._attribute(item, "data-uid")
            if user_id and user_id != target.user_id:
                return False
        return bool(target.content)

    async def _verify_reply(self, page: Any, target: Any, text: str, before: str) -> bool:
        for _ in range(3):
            await page.wait_for_timeout(400)
            after = await _inner_text(target)
            if text in after and text not in before:
                return True
            try:
                exact = page.get_by_text(text, exact=True)
                if await exact.count() and text not in before:
                    return await _is_visible(exact.nth(0))
            except Exception:
                pass
        return False

    async def _scroll_until_stable(self, page: Any, locator: Any, *, rounds: int) -> None:
        previous = -1
        for _ in range(rounds):
            count = await locator.count()
            if count == previous:
                break
            previous = count
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(350)

    async def _wait_for_search_surface(self, page: Any) -> str:
        """Wait for CSR search results or an explicit empty state.

        The current Douyin search shell is server-rendered with skeleton
        nodes; a short fixed delay can race the client-side result request and
        falsely report selector drift. This bounded DOM poll waits for a real
        result/empty marker without inspecting network payloads or using a
        screenshot.
        """

        empty_markers = ("暂无相关结果", "没有找到相关内容", "暂无内容", "无相关结果")
        for _ in range(24):
            for spec in SELECTORS.get("search.video_results", ()):
                try:
                    if await _make_locator(page, spec).count():
                        return "results"
                except Exception:
                    continue
            try:
                body = await _body_text(page)
            except Exception:
                body = ""
            if _contains_any(body, empty_markers):
                return "empty"
            await page.wait_for_timeout(500)
        return "timeout"

    async def _scroll_comments(self, page: Any, container: Any, items: Any) -> None:
        previous = -1
        for _ in range(5):
            count = await items.count()
            if count == previous:
                break
            previous = count
            try:
                await container.evaluate("node => { node.scrollTop = node.scrollHeight; return node.scrollHeight; }")
            except Exception:
                await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(350)

    async def _load_more_comments(self, page: Any, container: Any, items: Any) -> None:
        """Click an explicitly labelled DOM load-more control when present.

        Douyin does not expose a stable public pagination contract in the DOM.
        We therefore only follow visible, text-labelled controls and never
        manufacture a cursor or claim complete coverage.
        """

        for _ in range(5):
            before = await items.count()
            clicked = await self._click_optional(container, "comment.load_more", page=page, max_clicks=1)
            if not clicked:
                return
            await page.wait_for_timeout(450)
            if await items.count() <= before:
                return

    async def _expand_reply_threads(self, page: Any, items: Any) -> None:
        """Expand visible reply-thread controls using DOM/ARIA text only."""

        for index in range(await items.count()):
            await self._click_optional(items.nth(index), "comment.expand_replies", page=page, max_clicks=1)

    async def _click_optional(self, root: Any, name: str, *, page: Any, max_clicks: int) -> int:
        clicked = 0
        for spec in SELECTORS.get(name, ()):
            try:
                locator = _make_locator(root, spec)
                count = await locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                if not await _is_visible(candidate):
                    continue
                try:
                    await candidate.click()
                except Exception as exc:
                    await self.browser.capture_debug(
                        page,
                        action=name,
                        selector=spec.describe(),
                        error=str(exc),
                    )
                    raise DouyinPageParseError(
                        "真实抖音评论控件点击失败",
                        detail={"selector_name": name, "selector": spec.describe(), "error": str(exc)},
                    ) from exc
                clicked += 1
                await page.wait_for_timeout(250)
                if clicked >= max_clicks:
                    return clicked
        return clicked

    async def _attribute(self, locator: Any | None, name: str) -> str:
        if locator is None:
            return ""
        try:
            return (await locator.get_attribute(name) or "").strip()
        except Exception:
            return ""

    def _video_url(self, video_id_or_url: str) -> str:
        value = (video_id_or_url or "").strip()
        if not value:
            raise DouyinPageParseError("视频 ID 或 URL 不能为空")
        if value.startswith("http://") or value.startswith("https://"):
            hostname = (urlparse(value).hostname or "").lower()
            if hostname != "douyin.com" and not hostname.endswith(".douyin.com"):
                raise DouyinPageParseError("视频 URL 必须来自 douyin.com")
            return value
        return self._video_urls.get(value, f"{self.home_url.rstrip('/')}/video/{quote(value, safe='')}")


def _make_locator(root: Any, spec: SelectorSpec) -> Any:
    if spec.kind == "css":
        return root.locator(spec.value)
    if spec.kind == "role":
        return root.get_by_role(spec.value, name=spec.name, exact=False)
    if spec.kind == "label":
        return root.get_by_label(spec.value, exact=False)
    return root.get_by_text(spec.value, exact=False)


async def _nearest_result_container(link: Any) -> Any | None:
    try:
        parent = link.locator("xpath=ancestor::li[1]")
        if await parent.count():
            return parent
        parent = link.locator("xpath=..").first
        if await parent.count():
            return parent
    except Exception:
        pass
    return None


async def _is_visible(locator: Any) -> bool:
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def _inner_text(locator: Any) -> str:
    try:
        return (await locator.inner_text()).strip()
    except Exception:
        try:
            return (await locator.text_content() or "").strip()
        except Exception:
            return ""


async def _body_text(page: Any) -> str:
    body = page.locator("body")
    return await _inner_text(body)


async def _page_info(page: Any) -> tuple[str, str, str]:
    url = str(getattr(page, "url", ""))
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        dom_summary = (await _body_text(page))[:500]
    except Exception:
        dom_summary = ""
    return url, title, dom_summary


def _video_id_from_url(value: str) -> str:
    if not value:
        return ""
    path = urlparse(urljoin(DouyinBrowserManager.HOME_URL, value)).path.rstrip("/")
    match = re.search(r"/video/([^/]+)$", path)
    return unquote(match.group(1)) if match else ""


def _last_path_segment(value: str) -> str:
    return unquote(urlparse(value).path.rstrip("/").split("/")[-1]) if value else ""


def _comment_id_from_url(value: str) -> str:
    path = urlparse(urljoin(DouyinBrowserManager.HOME_URL, value)).path.rstrip("/")
    match = re.search(r"/comment/([^/]+)$", path)
    return unquote(match.group(1)) if match else ""


def _first_meaningful_line(value: str) -> str:
    for line in (part.strip() for part in value.splitlines()):
        if line and not re.fullmatch(r"[\d\s万亿,.+收藏评论分享赞点赞]+", line):
            return line
    return ""


def _metric(value: str, labels: Iterable[str]) -> int:
    if not value:
        return 0
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*([\d,.]+\s*[万亿]?)", value, re.I)
    if not match:
        return 0
    raw = match.group(1).replace(",", "").replace(" ", "")
    multiplier = 1
    if raw.endswith("万"):
        multiplier, raw = 10_000, raw[:-1]
    elif raw.endswith("亿"):
        multiplier, raw = 100_000_000, raw[:-1]
    try:
        return int(float(raw) * multiplier)
    except ValueError:
        return 0


def _parse_datetime(value: str) -> datetime | None:
    match = re.search(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})", value or "")
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _dom_cursor(value: int) -> str:
    return f"dom:{max(0, value)}"


def _parse_dom_cursor(value: str | None) -> int:
    if not value:
        return 0
    raw = value.removeprefix("dom:")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError) as exc:
        raise DouyinPageParseError("评论分页游标无效", detail={"cursor": value}) from exc


def _contains_any(value: str, needles: Iterable[str]) -> bool:
    return any(needle.lower() in value for needle in needles)
