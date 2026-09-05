"""Offline DOM contracts for the real Douyin Playwright provider.

These tests deliberately substitute locator/page behavior only.  They do not
stand in for a Douyin account, network response, or synthetic business
record; live login and selector calibration remain an E2E acceptance gate.
"""

from contextlib import asynccontextmanager

import pytest

from app.providers.base import CommentDTO
from app.core.config import PROJECT_ROOT
from app.providers.douyin.browser_manager import DouyinBrowserManager
from app.providers.douyin.playwright_provider import DouyinPlaywrightProvider


class LocatorDouble:
    """Small locator substitute with only the DOM operations under contract."""

    def __init__(self, *, text="", attrs=None, visible=True, on_click=None, children=None):
        self.text = text
        self.attrs = attrs or {}
        self.visible = visible
        self.on_click = on_click
        self.children = children or {}
        self.clicks = 0
        self.fill_calls = []

    async def count(self):
        return 1

    def nth(self, _index):
        return self

    async def is_visible(self):
        return self.visible

    async def click(self):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    async def inner_text(self):
        return self.text

    async def text_content(self):
        return self.text

    async def get_attribute(self, name):
        return self.attrs.get(name)

    def locator(self, selector):
        return self.children.get(selector, EmptyLocator())

    def get_by_role(self, *_args, **_kwargs):
        return EmptyLocator()

    def get_by_label(self, *_args, **_kwargs):
        return EmptyLocator()

    def get_by_text(self, *_args, **_kwargs):
        return EmptyLocator()

    async def fill(self, value):
        self.fill_calls.append(value)


class EmptyLocator(LocatorDouble):
    def __init__(self):
        super().__init__(visible=False)

    async def count(self):
        return 0


class PageDouble(LocatorDouble):
    def __init__(self, selectors=None):
        super().__init__()
        self.selectors = selectors or {}
        self.keyboard = KeyboardDouble()
        self.mouse = MouseDouble()

    def locator(self, selector):
        return self.selectors.get(selector, EmptyLocator())

    async def wait_for_timeout(self, _milliseconds):
        return None

    def get_by_text(self, text, *, exact=False):
        return self.selectors.get(f"text:{text}:{exact}", EmptyLocator())


class KeyboardDouble:
    def __init__(self):
        self.typed = []

    async def type(self, value, delay=0):
        self.typed.append((value, delay))


class MouseDouble:
    async def wheel(self, *_args):
        return None


class BrowserDouble:
    def __init__(self, page):
        self.page = page
        self.is_running = True

    async def start(self, **_kwargs):
        self.is_running = True
        return self.page

    async def close(self):
        self.is_running = False

    @asynccontextmanager
    async def locked_page(self):
        yield self.page

    async def capture_debug(self, *_args, **_kwargs):
        return None

    async def valid_session_cookie_names(self, *_args, **_kwargs):
        return set()

    async def is_healthy(self):
        return self.is_running


@pytest.mark.asyncio
async def test_comment_surface_prefers_comment_main_content_without_opening_feed():
    container = LocatorDouble()
    open_button = LocatorDouble()
    page = PageDouble(
        {
            ".comment-mainContent": container,
            '[data-e2e="feed-comment-icon"]': open_button,
        }
    )
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(page))

    result = await provider._prepare_comment_surface(page, action="contract")

    assert result is container
    assert open_button.clicks == 0
    assert provider.last_dom_trace["page_shape"] == "video_detail_dom"
    assert provider.last_dom_trace["comment_open_action"] == "existing_dom_surface"
    assert provider.last_dom_trace["comment.container"] == "css='.comment-mainContent'"


@pytest.mark.asyncio
async def test_comment_surface_opens_feed_comment_icon_when_dom_is_not_open():
    container = LocatorDouble()
    state = {"open": False}

    def open_comments():
        state["open"] = True

    open_button = LocatorDouble(on_click=open_comments)

    class DynamicPage(PageDouble):
        def locator(self, selector):
            if selector == ".comment-mainContent":
                return container if state["open"] else EmptyLocator()
            return self.selectors.get(selector, EmptyLocator())

    page = DynamicPage({'[data-e2e="feed-comment-icon"]': open_button})
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(page))

    result = await provider._prepare_comment_surface(page, action="contract")

    assert result is container
    assert open_button.clicks == 1
    assert provider.last_dom_trace["page_shape"] == "feed_modal_dom"
    assert provider.last_dom_trace["comment_open_action"] == "click:css='[data-e2e=\"feed-comment-icon\"]'"
    assert provider.last_dom_trace["comment.container"] == "css='.comment-mainContent'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("暂无评论", "no_comments"),
        ("主播已关闭评论", "comments_closed"),
    ],
)
async def test_comment_empty_state_is_detected_from_dom_text(message, expected):
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(PageDouble()))
    container = LocatorDouble(text=message)

    assert await provider._comment_empty_state(container, page=PageDouble()) == expected


@pytest.mark.asyncio
async def test_get_comments_returns_partial_empty_result_for_video_without_comments():
    container = LocatorDouble(text="暂无评论")
    page = PageDouble()
    browser = BrowserDouble(page)
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    async def no_op(*_args, **_kwargs):
        return None

    provider._require_login = no_op
    provider._navigate_if_needed = no_op
    provider._prepare_comment_surface = lambda *_args, **_kwargs: _async_value(container)

    result = await provider.get_comments("https://www.douyin.com/video/real-video")

    assert result.items == []
    assert result.items_received == 0
    assert result.coverage_status == "partial"
    assert result.has_more is False
    assert provider.last_dom_trace["comment.empty_state"] == "no_comments"


@pytest.mark.asyncio
async def test_search_surface_returns_empty_only_for_explicit_dom_empty_marker():
    page = PageDouble({"body": LocatorDouble(text="没有找到相关内容")})
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(page))

    assert await provider._wait_for_search_surface(page) == "empty"


@pytest.mark.asyncio
async def test_comment_matches_tooltip_anchor_id_as_stable_dom_identity():
    anchor = LocatorDouble(attrs={"id": "tooltip_comment-42"})
    item = LocatorDouble(text="评论文本", children={"[id^=\"tooltip_\"]": anchor})
    target = CommentDTO("douyin", "comment-42", "", "", "", "")
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(PageDouble()))

    assert await provider._comment_matches(item, target) is True


@pytest.mark.asyncio
async def test_comment_parser_reads_current_body_class_and_tooltip_id():
    item = LocatorDouble(
        text="客户昵称\n真实评论正文",
        children={
            '[class~="FduGc_lz"]': LocatorDouble(text="真实评论正文"),
            '[id^="tooltip_"]': LocatorDouble(attrs={"id": "tooltip_real-comment-7"}),
            'a[href*="/user/"]': LocatorDouble(text="客户昵称", attrs={"href": "/user/real-user-7"}),
        },
    )
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(PageDouble()))

    result = await provider._parse_comment(item, "https://www.douyin.com/video/real-video", page=PageDouble())

    assert result is not None
    assert result.content == "真实评论正文"
    assert result.nickname == "客户昵称"
    assert result.comment_id == "real-comment-7"
    assert result.id_source == "dom_attribute"


@pytest.mark.asyncio
async def test_comment_parser_uses_stable_fingerprint_without_platform_identity():
    item = LocatorDouble(
        text="客户昵称\n没有稳定 ID 的评论",
        children={'[class~="FduGc_lz"]': LocatorDouble(text="没有稳定 ID 的评论")},
    )
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(PageDouble()))

    result = await provider._parse_comment(item, "https://www.douyin.com/video/real-video", page=PageDouble())

    assert result is not None
    assert len(result.comment_id) == 64
    assert result.id_source == "fingerprint"
    assert result.comment_id == (await provider._parse_comment(item, "https://www.douyin.com/video/real-video", page=PageDouble())).comment_id


@pytest.mark.asyncio
async def test_reply_input_contract_prefers_draft_js_contenteditable():
    draft = LocatorDouble()
    textarea = LocatorDouble()
    page = PageDouble(
        {
            '.public-DraftEditor-content[contenteditable="true"]': draft,
            'textarea[placeholder*="回复"]': textarea,
        }
    )
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(page))

    result = await provider._find(page, "comment.reply_input", page=page)

    assert result is draft
    assert textarea.fill_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector",
    [
        ".comment-input-container .PPsJmqBy",
        '[id^="placeholder-"]',
    ],
)
async def test_reply_target_accepts_standalone_and_modal_dom_modes(selector):
    target = LocatorDouble(text="回复@客户")
    page = PageDouble({selector: target})
    provider = DouyinPlaywrightProvider(browser_manager=BrowserDouble(page))

    await provider._wait_for_reply_target(page)


@pytest.mark.asyncio
async def test_reply_flow_types_into_draft_editor_via_keyboard_not_fill():
    draft = LocatorDouble()
    reply_button = LocatorDouble()
    send_button = LocatorDouble()
    target = LocatorDouble(
        text="客户 原始评论",
        children={
            ".LpRGQ4Gi": reply_button,
            ".f5hSYimo.siaMKBB_": send_button,
        },
    )
    page = PageDouble(
        {
            '.public-DraftEditor-content[contenteditable="true"]': draft,
            ".comment-input-container .PPsJmqBy": LocatorDouble(text="回复@客户"),
        }
    )
    browser = BrowserDouble(page)
    provider = DouyinPlaywrightProvider(browser_manager=browser)
    items = LocatorDouble()

    async def no_op(*_args, **_kwargs):
        return None

    async def find_all(*_args, **_kwargs):
        return items

    async def locate(*_args, **_kwargs):
        return target, 1

    async def verify(*_args, **_kwargs):
        return True

    provider._require_login = no_op
    provider._navigate_if_needed = no_op
    provider._prepare_comment_surface = no_op
    provider._find_all = find_all
    provider._locate_comment_with_scrolling = locate
    provider._verify_reply = verify

    result = await provider.reply_comment(
        "https://www.douyin.com/video/real-video",
        CommentDTO("douyin", "comment-42", "", "客户", "", "原始评论"),
        "请留下联系方式",
    )

    assert result.verified is True
    assert page.keyboard.typed == [("请留下联系方式", 40)]
    assert draft.fill_calls == []


def test_sub_comments_capability_is_explicitly_unclaimed():
    assert DouyinPlaywrightProvider.capabilities["sub_comments"] is False


def test_relative_douyin_profile_is_anchored_to_repository_root():
    manager = DouyinBrowserManager(profile_dir="./data/browser/douyin")

    assert manager.profile_dir == (PROJECT_ROOT / "data" / "browser" / "douyin").resolve()


def test_browser_restore_prefers_existing_douyin_page_over_blank_tab():
    blank = type("Page", (), {"url": "about:blank"})()
    douyin = type("Page", (), {"url": "https://www.douyin.com/"})()

    assert DouyinBrowserManager._preferred_page([blank, douyin]) is douyin


@pytest.mark.asyncio
async def test_provider_reopens_persistent_browser_after_process_restart():
    browser = BrowserDouble(PageDouble())
    browser.is_running = False
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    await provider.ensure_browser_started()

    assert browser.is_running is True


@pytest.mark.asyncio
async def test_provider_reopens_persistent_browser_after_transport_disconnect():
    browser = BrowserDouble(PageDouble())
    browser.is_healthy = lambda: _async_value(False)
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    await provider.ensure_browser_started()

    assert browser.is_running is True


@pytest.mark.asyncio
async def test_navigation_does_not_require_slow_client_resources_before_dom_checks():
    calls = []

    class NavigationPage:
        url = "about:blank"

        async def goto(self, url, **kwargs):
            self.url = url
            calls.append(("goto", url, kwargs))

        async def wait_for_load_state(self, state, **kwargs):
            calls.append(("wait_for_load_state", state, kwargs))

    class NavigationBrowser:
        async def capture_debug(self, *_args, **_kwargs):
            raise AssertionError("导航成功时不应捕获调试错误")

    provider = DouyinPlaywrightProvider(browser_manager=NavigationBrowser())
    page = NavigationPage()

    await provider._navigate(page, "https://www.douyin.com/search/长沙装修?type=video", action="contract")

    assert calls[0][0] == "goto"
    assert calls[0][2]["wait_until"] == "commit"
    assert calls[0][2]["timeout"] == 20_000
    assert calls[1][0:2] == ("wait_for_load_state", "domcontentloaded")


@pytest.mark.asyncio
async def test_browser_start_navigation_uses_document_commit_boundary():
    calls = []

    class NavigationPage:
        url = "about:blank"

        async def goto(self, url, **kwargs):
            self.url = url
            calls.append(("goto", url, kwargs))

        async def wait_for_load_state(self, state, **kwargs):
            calls.append(("wait_for_load_state", state, kwargs))

    manager = DouyinBrowserManager(profile_dir="./data/browser/douyin")
    await manager._goto_unlocked(NavigationPage(), "https://www.douyin.com/", action="contract")

    assert calls[0][2]["wait_until"] == "commit"
    assert calls[0][2]["timeout"] == 20_000
    assert calls[1][0:2] == ("wait_for_load_state", "domcontentloaded")


@pytest.mark.asyncio
async def test_browser_manager_passes_configured_proxy_to_chromium(monkeypatch):
    class Context:
        pages = [type("Page", (), {"url": "about:blank"})()]

    class BrowserType:
        def __init__(self):
            self.options = None

        async def launch_persistent_context(self, **options):
            self.options = options
            return Context()

    class Playwright:
        def __init__(self, browser_type):
            self.chromium = browser_type

    class PlaywrightManager:
        def __init__(self, playwright):
            self.playwright = playwright

        async def start(self):
            return self.playwright

    browser_type = BrowserType()
    manager = DouyinBrowserManager(
        profile_dir="./data/browser/test-proxy",
        proxy_server="http://127.0.0.1:7897",
        playwright_factory=lambda: PlaywrightManager(Playwright(browser_type)),
    )

    await manager._start_unlocked()

    assert browser_type.options["proxy"] == {"server": "http://127.0.0.1:7897"}
    await manager.close()


@pytest.mark.asyncio
async def test_login_detection_prefers_restored_session_cookies_over_hidden_login_panel():
    page = PageDouble({
        '[id^="login-full-panel"]': LocatorDouble(),
        '[data-e2e="user-avatar"]': EmptyLocator(),
    })
    browser = BrowserDouble(page)
    browser.valid_session_cookie_names = lambda *_args, **_kwargs: _async_value({"sessionid_ss", "uid_tt_ss"})
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    assert await provider._detect_login_status(page) == "LOGGED_IN"


@pytest.mark.asyncio
async def test_login_detection_ignores_body_verification_script_text_when_session_is_restored():
    page = PageDouble({"body": LocatorDouble(text="正常页面内容 验证码 captcha")})
    browser = BrowserDouble(page)
    browser.valid_session_cookie_names = lambda *_args, **_kwargs: _async_value({"sessionid_ss", "uid_tt_ss"})
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    assert await provider._detect_login_status(page) == "LOGGED_IN"


@pytest.mark.asyncio
async def test_login_detection_keeps_visible_security_challenge_as_verification_required():
    page = PageDouble({
        "body": LocatorDouble(text="安全验证"),
        '[class*="captcha" i]': LocatorDouble(),
    })
    browser = BrowserDouble(page)
    browser.valid_session_cookie_names = lambda *_args, **_kwargs: _async_value({"sessionid_ss", "uid_tt_ss"})
    provider = DouyinPlaywrightProvider(browser_manager=browser)

    assert await provider._detect_login_status(page) == "VERIFICATION_REQUIRED"


async def _async_value(value):
    return value
