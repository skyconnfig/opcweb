"""Windows event-loop factory for Playwright's async subprocess transport."""

from __future__ import annotations

import asyncio
import sys


def create_loop(*, use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """Use a subprocess-capable loop for the async Playwright provider."""

    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()
