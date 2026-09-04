from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProviderHealth:
    status: str
    message: str


@dataclass
class VideoDTO:
    platform: str
    video_id: str
    title: str
    description: str
    creator: str
    url: str
    cover: str
    publish_time: datetime | None
    likes: int
    comments: int
    shares: int
    collects: int
    keyword: str


@dataclass
class CommentDTO:
    platform: str
    comment_id: str
    user_id: str
    nickname: str
    profile_url: str
    content: str
    created_at: datetime | None = None


@dataclass
class CommentScanResult:
    items: list[CommentDTO] = field(default_factory=list)
    coverage_status: str = "unknown"
    items_received: int = 0
    next_cursor: str | None = None
    has_more: bool = False


class BaseContentProvider(ABC):
    name: str = "base"
    platform: str = "douyin"
    capabilities: dict[str, bool] = {}

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    async def search_videos(self, keyword: str, limit: int) -> list[VideoDTO]: ...

    @abstractmethod
    async def get_video(self, video_id: str) -> VideoDTO | None: ...

    @abstractmethod
    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult: ...

    async def get_creator(self, creator_id: str) -> dict:
        return {"creator_id": creator_id, "supported": self.capabilities.get("creator", False)}

