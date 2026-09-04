from app.services.radar_service import RadarService


class ProjectScanTask:
    """Task facade kept separate so a scheduler can invoke the same resumable service."""

    def __init__(self, service: RadarService):
        self.service = service

    async def run(self, task_id: int, full: bool = False):
        await self.service.run_task(task_id, full=full)

