"""Persistent Playwright lifecycle and per-account serialization."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, ClassVar

from app.core.config import PROJECT_ROOT
from app.providers.douyin.exceptions import DouyinBrowserError, DouyinPageLoadError


class DouyinBrowserManager:
    """Own one persistent browser profile and serialize its critical actions.

    A lock is shared by all manager instances that point to the same resolved
    profile directory.  This prevents a second provider instance from opening
    a concurrent context for the same Douyin account.
    """

    HOME_URL = "https://www.douyin.com/"
    _account_locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self,
        *,
        profile_dir: str | Path | None = None,
        channel: str | None = None,
        headless: bool = False,
        proxy_server: str | None = None,
        debug_dir: str | Path | None = None,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        configured_profile = profile_dir or os.getenv("DOUYIN_PROFILE_DIR") or os.getenv("DOUYIN_BROWSER_PROFILE_DIR")
        profile_path = Path(configured_profile or "data/browser/douyin")
        if not profile_path.is_absolute():
            profile_path = PROJECT_ROOT / profile_path
        self.profile_dir = profile_path.resolve()
        self.channel = (channel or os.getenv("DOUYIN_BROWSER_CHANNEL") or "chromium").lower()
        self.headless = headless if os.getenv("DOUYIN_HEADLESS") is None else _env_bool("DOUYIN_HEADLESS", headless)
        # Chromium does not consistently inherit HTTP(S)_PROXY from the
        # parent process on Windows. Prefer an explicit app setting, then the
        # conventional local environment proxy used by the API/CLI.
        self.proxy_server = (
            proxy_server
            or os.getenv("DOUYIN_PROXY_SERVER")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("ALL_PROXY")
            or ""
        ).strip()
        self.debug_dir = Path(debug_dir or os.getenv("DOUYIN_DEBUG_DIR") or "data/debug").resolve()
        self._playwright_factory = playwright_factory
        self._account_key = str(self.profile_dir).casefold()
        self._lock = self._account_locks.setdefault(self._account_key, asyncio.Lock())
        self._playwright: Any = None
        self._context: Any = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def is_running(self) -> bool:
        return self._context is not None

    async def is_healthy(self) -> bool:
        """Check whether the Playwright context still has a live browser.

        ``launch_persistent_context`` can leave a Python context object behind
        after Chromium or the Playwright transport has exited.  Treating that
        stale object as running makes the next login check fail with a generic
        ``ERROR`` and prevents the persisted profile from being reopened.
        """

        context = self._context
        if context is None:
            return False
        try:
            browser = getattr(context, "browser", None)
            is_connected = getattr(browser, "is_connected", None)
            if callable(is_connected) and not is_connected():
                return False
            pages = list(context.pages)
            if not pages:
                return False
            page = self._preferred_page(pages)
            is_closed = getattr(page, "is_closed", None)
            if callable(is_closed) and is_closed():
                return False
            return True
        except Exception:
            return False

    async def start(self, *, url: str = HOME_URL) -> Any:
        """Start/reuse the visible persistent context and open ``url``."""

        async with self._lock:
            page = await self._start_unlocked()
            if url and page.url.rstrip("/") != url.rstrip("/"):
                await self._goto_unlocked(page, url, action="browser.start")
            return page

    async def _start_unlocked(self) -> Any:
        if self._context is not None:
            pages = list(self._context.pages)
            if pages:
                return self._preferred_page(pages)
            return await self._context.new_page()

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - exercised on bad install
            raise DouyinBrowserError(
                "Playwright 未安装，请执行 uv pip install -e '.[test]' && playwright install chromium"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        manager = None
        try:
            manager = (self._playwright_factory or async_playwright)()
            self._playwright = await manager.start()
            browser_type = self._playwright.chromium
            launch_options: dict[str, Any] = {
                "headless": self.headless,
                "accept_downloads": False,
            }
            channel = _playwright_channel(self.channel)
            if channel:
                launch_options["channel"] = channel
            if self.proxy_server:
                launch_options["proxy"] = {"server": self.proxy_server}
            self._context = await browser_type.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                **launch_options,
            )
            pages = list(self._context.pages)
            return self._preferred_page(pages) if pages else await self._context.new_page()
        except Exception as exc:
            await self._dispose_unlocked()
            if isinstance(exc, DouyinBrowserError):
                raise
            raise DouyinBrowserError(
                f"无法启动 Douyin 浏览器: {type(exc).__name__}: {exc!r}",
                detail={"profile_dir": str(self.profile_dir), "channel": self.channel, "proxy_configured": bool(self.proxy_server), "error_type": type(exc).__name__},
            ) from exc

    async def open(self, url: str) -> Any:
        """Navigate the current profile to a real Douyin URL."""

        async with self._lock:
            page = await self._start_unlocked()
            await self._goto_unlocked(page, url, action="browser.open")
            return page

    async def _goto_unlocked(self, page: Any, url: str, *, action: str) -> None:
        try:
            # Douyin keeps some client resources pending for a long time. A
            # committed document is enough to restore the persistent browser;
            # callers perform the authoritative login and DOM checks after
            # navigation. Waiting for every resource here can make a valid
            # session look unavailable after an API restart.
            await page.goto(url, wait_until="commit", timeout=20_000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
        except Exception as exc:
            await self.capture_debug(page, action=action, selector=url, error=str(exc))
            raise DouyinPageLoadError(
                f"抖音页面加载失败: {url}",
                detail={"url": url, "action": action, "error": str(exc)},
            ) from exc

    async def current_page(self) -> Any:
        if self._context is None:
            raise DouyinBrowserError("Douyin 浏览器尚未启动")
        pages = list(self._context.pages)
        if not pages:
            raise DouyinBrowserError("Douyin 浏览器没有可用页面")
        return self._preferred_page(pages)

    @staticmethod
    def _preferred_page(pages: list[Any]) -> Any:
        """Prefer an existing Douyin tab when restoring a persistent session.

        Chromium can restore several tabs, including an ``about:blank`` tab
        created by the launcher.  Picking ``pages[0]`` can therefore inspect
        the wrong document and make a valid persisted session look logged out.
        The page order is otherwise left untouched so the browser remains
        predictable for a fresh profile.
        """

        for page in pages:
            url = str(getattr(page, "url", ""))
            if "douyin.com" in url.casefold():
                return page
        return pages[0]

    async def valid_session_cookie_names(self, url: str = HOME_URL) -> set[str]:
        """Return only valid Douyin session-cookie names, never values."""

        if self._context is None:
            return set()
        try:
            cookies = await self._context.cookies(url)
        except Exception:
            return set()
        now = time.time()
        # Douyin may persist either the regular or the ``_ss`` session
        # variant depending on the browser channel and login flow.  Return
        # names only; cookie values must never leave the browser context.
        session_names = {"sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss"}
        valid: set[str] = set()
        for cookie in cookies:
            name = str(cookie.get("name", ""))
            if name not in session_names:
                continue
            try:
                expires = float(cookie.get("expires", -1))
            except (TypeError, ValueError):
                continue
            if expires in (-1, 0) or expires > now:
                valid.add(name)
        return valid

    @asynccontextmanager
    async def locked_page(self) -> AsyncIterator[Any]:
        """Hold the per-account lock for one complete browser action."""

        async with self._lock:
            yield await self.current_page()

    async def close(self) -> None:
        async with self._lock:
            await self._dispose_unlocked()

    async def _dispose_unlocked(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._playwright = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def capture_debug(
        self,
        page: Any,
        *,
        action: str,
        selector: str,
        error: str,
    ) -> Path | None:
        """Save HTML and redacted metadata for selector/page failures."""

        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = self.debug_dir / stamp
            target.mkdir(parents=True, exist_ok=True)
            html = await page.content()
            (target / "page.html").write_text(html, encoding="utf-8")
            url = str(getattr(page, "url", ""))
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            (target / "meta.json").write_text(
                json.dumps(
                    {"url": url, "title": title, "action": action, "selector": selector, "error": error},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return target
        except Exception:
            return None


def _playwright_channel(channel: str) -> str | None:
    if channel in {"", "chromium", "bundled-chromium", "default"}:
        return None
    if channel in {"edge", "msedge"}:
        return "msedge"
    if channel in {"chrome", "chrome-beta", "chrome-dev", "chrome-canary"}:
        return channel
    raise DouyinBrowserError(
        f"不支持的浏览器 channel: {channel}",
        detail={"allowed": ["chromium", "msedge", "chrome", "chrome-beta", "chrome-dev", "chrome-canary"]},
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
