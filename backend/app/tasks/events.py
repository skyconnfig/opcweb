from app.services.event_bus import event_bus


async def publish_task_event(event: dict):
    await event_bus.publish(event)

