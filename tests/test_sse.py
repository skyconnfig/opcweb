from starlette.requests import Request

from app.main import _resolve_sse_cursor
from app.services.event_bus import sse_line


def _request_with_last_event_id(value: str) -> Request:
    return Request({"type": "http", "headers": [(b"last-event-id", value.encode("ascii"))]})


def test_sse_line_emits_standard_event_id_field():
    line = sse_line({"id": 8, "event_type": "lead.detected", "message": "发现潜客"})

    assert line.startswith("id: 8\ndata: ")
    assert line.endswith("\n\n")


def test_sse_cursor_prefers_the_newest_query_or_header_cursor():
    assert _resolve_sse_cursor(_request_with_last_event_id("12"), 8) == 12
    assert _resolve_sse_cursor(_request_with_last_event_id("4"), 8) == 8


def test_sse_cursor_ignores_invalid_header_cursor():
    assert _resolve_sse_cursor(_request_with_last_event_id("not-a-number"), 8) == 8
