# Third-party research and integration boundary

本项目只吸收公开架构/接口层面的经验，不复制参考项目源码；`.references/` 被 `.gitignore` 排除。

| 项目 | 用途 | 主项目是否包含源码 | 许可证/边界 |
|---|---|---:|---|
| `yangmaoxin/social-harvest` | 研究 checkpoint、task event、report、平台 registry 与可恢复 runner；兼容外部 task report | 否 | 以仓库当前声明为准；主项目仅做独立 Adapter |
| `yuanjian068yuan/opc-comment-lead-radar` | 研究业务工作流、关键词发现、购买信号与人工跟进话术 | 否 | MIT；不复制其产品或 Skill 源码 |
| `hulunfu/douyin-comments-crawler` | 可选外部 HTTP Provider，调用 `/health` 与评论接口 | 否 | 仅通过 HTTP；用户自行确认授权、账号与平台规则 |
| `NanmiCoder/MediaCrawler` | 可选独立外部研究数据源 | 否 | 当前仓库许可证/平台规则限制商业内置；绝不 import、vendor 或打包其源码 |
| `hycarbon-b/crawl-douyin` | 研究抖音 modal/独立视频页的 DOM 评论与回复交互契约 | 否 | 仅保留独立 selector/交互经验；不复制源码、脚本或反风控逻辑，商业使用前需自行核对其当前许可证与平台规则 |
