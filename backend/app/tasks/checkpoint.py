from app.models import TaskCheckpoint


def checkpoint_snapshot(checkpoint: TaskCheckpoint) -> dict:
    return {"last_keyword_id": checkpoint.last_keyword_id, "last_video_id": checkpoint.last_video_id, "last_comment_cursor": checkpoint.last_comment_cursor, "processed_comment_ids": checkpoint.processed_comment_ids}

