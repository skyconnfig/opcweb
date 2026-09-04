from apscheduler.schedulers.asyncio import AsyncIOScheduler


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="Asia/Shanghai")

