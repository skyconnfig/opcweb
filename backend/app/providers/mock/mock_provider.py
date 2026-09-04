from datetime import datetime, timedelta, timezone

from app.providers.base import BaseContentProvider, CommentDTO, CommentScanResult, ProviderHealth, VideoDTO


HIGH_COMMENTS = [
    "长沙有没有靠谱一点的？",
    "我家120平，15万能下来吗？",
    "旧房翻新大概多少钱？",
    "望城这边可以做吗？",
    "有没有联系方式？",
    "准备年底装修，不知道怎么选公司。",
    "被上一家公司坑过，增项太多。",
    "我在岳麓区，最近想把厨房和卫生间一起翻新",
    "100平全屋装修预算怎么规划？",
    "能先上门量房看看吗？",
]
ORDINARY_COMMENTS = ["讲得很好", "收藏了", "666", "哈哈哈", "主播好帅", "路过", "支持一下", "学到了"]


class MockProvider(BaseContentProvider):
    name = "Mock Provider"
    platform = "douyin"
    capabilities = {"keyword_search": True, "video_detail": True, "comments": True, "sub_comments": False, "creator": True}

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth("connected", "Demo 数据源已就绪；数据来自本地固定样本")

    async def search_videos(self, keyword: str, limit: int) -> list[VideoDTO]:
        now = datetime.now(timezone.utc)
        return [
            VideoDTO("douyin", f"mock-{index:03d}", f"{keyword}｜长沙装修人最容易踩的 {index % 8 + 3} 个坑", "公开 Demo 视频", f"长沙装修观察员{index}", f"https://www.douyin.com/video/mock-{index:03d}", "", now - timedelta(days=index), 1200 + index * 730, 80 + index * 17, 16 + index * 3, 40 + index * 4, keyword)
            for index in range(1, min(limit, 20) + 1)
        ]

    async def get_video(self, video_id: str) -> VideoDTO | None:
        result = await self.search_videos("装修", 20)
        return next((item for item in result if item.video_id == video_id), None)

    async def get_comments(self, video_id: str, cursor: str | None = None) -> CommentScanResult:
        video_number = int(video_id.rsplit("-", 1)[-1]) if video_id.rsplit("-", 1)[-1].isdigit() else 1
        items: list[CommentDTO] = []
        for index in range(15):
            global_index = (video_number - 1) * 15 + index
            if global_index < len(HIGH_COMMENTS):
                content = HIGH_COMMENTS[global_index]
                user_id = f"buyer-{global_index:03d}"
                nickname = ["装修小白", "想翻新的阿敏", "长沙业主老周", "望城小何", "小满准备装修", "被增项坑过", "岳麓新家", "预算15万"][global_index % 8]
            else:
                content = ORDINARY_COMMENTS[global_index % len(ORDINARY_COMMENTS)]
                user_id = f"viewer-{global_index:03d}"
                nickname = f"路过的朋友{global_index}"
            items.append(CommentDTO("douyin", f"comment-{global_index:03d}", user_id, nickname, f"https://www.douyin.com/user/{user_id}", content))
        return CommentScanResult(items=items, coverage_status="partial", items_received=len(items), next_cursor=None, has_more=False)

