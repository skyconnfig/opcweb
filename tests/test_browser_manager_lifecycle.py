"""Lifecycle tests for the persistent, real-browser manager."""

import pytest

from app.providers.douyin.browser_manager import DouyinBrowserManager


@pytest.mark.asyncio
async def test_browser_manager_reopens_a_stale_context_before_reusing_profile(tmp_path):
    class Page:
        def __init__(self, url):
            self.url = url

        def is_closed(self):
            return False

    class Browser:
        def __init__(self, connected):
            self.connected = connected

        def is_connected(self):
            return self.connected

    class Context:
        def __init__(self, connected):
            self.browser = Browser(connected)
            self.pages = [Page("https://www.douyin.com/")]
            self.closed = False
            self.events = []

        async def close(self):
            self.closed = True

        def on(self, event, callback):
            self.events.append((event, callback))

    stale = Context(connected=False)
    fresh = Context(connected=True)

    class BrowserType:
        async def launch_persistent_context(self, **_kwargs):
            return fresh

    class Transport:
        chromium = BrowserType()
        stopped = False

        async def start(self):
            return self

        async def stop(self):
            self.stopped = True

    manager = DouyinBrowserManager(profile_dir=tmp_path / "profile", playwright_factory=Transport)
    manager._context = stale
    old_transport = Transport()
    manager._playwright = old_transport

    page = await manager.start()

    assert page is fresh.pages[0]
    assert stale.closed is True
    assert old_transport.stopped is True
    assert manager._context is fresh
    assert fresh.events and fresh.events[0][0] == "close"
