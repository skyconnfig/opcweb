"""DTOs specific to the DOM-backed Douyin provider."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.providers.base import CommentDTO, CommentScanResult, VideoDTO


class LoginStatus(StrEnum):
    LOGGED_OUT = "LOGGED_OUT"
    WAITING_LOGIN = "WAITING_LOGIN"
    LOGGED_IN = "LOGGED_IN"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


# Explicit descriptive alias for callers that prefer the domain name.
DouyinLoginStatus = LoginStatus


class ReplyStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SENT_UNVERIFIED = "SENT_UNVERIFIED"


@dataclass
class DouyinVideoDTO(VideoDTO):
    """A base-compatible video DTO with DOM provenance."""

    source: str = "dom"

    @property
    def platform_video_id(self) -> str:
        return self.video_id


@dataclass
class DouyinCommentDTO(CommentDTO):
    """A base-compatible comment DTO with stable-ID provenance."""

    video_url: str = ""
    comment_url: str = ""
    is_reply: bool = False
    like_count: int = 0
    id_source: str = "dom"


@dataclass
class ReplyResult:
    status: ReplyStatus
    platform: str
    video_url: str
    comment_id: str
    reply_text: str
    verified: bool
    detail: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.verified


# Make the result type discoverable alongside the base DTOs without changing
# the existing provider contract.
DouyinCommentScanResult = CommentScanResult
