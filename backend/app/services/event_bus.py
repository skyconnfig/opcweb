import asyncio
import json
from datetime import datetime, timezone


class EventBus:
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=200)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        self.subscribers.discard(queue)

    async def publish(self, event: dict):
        event = {"id": event.get("id"), "project_id": event.get("project_id"), "event_type": event.get("event_type", "radar.update"), "message": event.get("message", ""), "payload": event.get("payload", {}), "created_at": event.get("created_at", datetime.now(timezone.utc).isoformat())}
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass


event_bus = EventBus()


def sse_line(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
