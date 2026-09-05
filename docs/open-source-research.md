# 开源项目研究记录

研究日期：2026-09-04。四个仓库均通过 shallow clone 读取当前默认分支 `main`；源代码目录位于本地 `.references/`，该目录不会提交。

## Social Harvest

重点查看了 `runner/checkpoint.js`、`runner/events.js`、`runner/reports.js`、`scripts/task-runner.js`、`scripts/lib/platform-registry.js` 与 task report schema。值得借鉴的是长任务选项标准化、断点游标/完成项/失败项持久化、带前缀的结构化事件、任务状态与报告分离，以及通过平台 registry/sink 解耦采集与入库。本项目对应实现为 `TaskCheckpoint`、`TaskEvent`、`TaskReport` 和 `TaskEngine`；不会复制其代码。

## OPC Comment Lead Radar

重点查看 `skills/ppxc-find-customers/SKILL.md` 及示例。值得借鉴的是先理解产品/目标客户，再生成关键词，随后筛选购买信号并输出客户池、跟进话术的异步工作流；本项目拆成 `IndustryAgent`、`KeywordAgent`、`LeadJudgeAgent`、`PersonaAgent`。它的在线服务/商业流程不作为本项目依赖。

## douyin-comments-crawler

重点查看 `API接口说明.md` 与 API server 路由。其公开 HTTP 形态是 `/health`、`/api/keyword/comments`、`/api/video/comments`、`/api/user/comments`。本项目的 `DouyinCommentsCrawlerExternalProvider` 只发送规范化 HTTP 请求并转换为统一 DTO，不处理对方内部 JSON，不复制浏览器采集逻辑。

## MediaCrawler

重点查看 `docs/项目架构文档.md`、配置和 README。它提供 search/detail/creator 模式、多平台抽象、评论抓取和 SQLite/JSONL 等存储设计启发。本项目只提供 `MediaCrawlerExternalProvider`，通过外部命令输出 JSON/JSONL 再归一化；不 import 内部 Python 模块，不 vendor，不把它作为商业版内置依赖。

## hycarbon-b/crawl-douyin

研究日期：2026-09-04；本地参考快照为默认分支 `master`，commit `cdde8d7bfec3b018454705240fa0df6550ece6db`。只阅读其公开脚本和 DOM 经验，不复制源码、脚本或反风控策略。

本项目吸收的独立实现经验仅限于页面交互契约：modal 视频页需要先点击 `[data-e2e="feed-comment-icon"]` 才会出现评论区；评论列表滚动容器是 `.comment-mainContent`；评论稳定锚点可从 `#tooltip_<comment_id>` 向上关联 `[data-e2e="comment-item"]`；Draft.js 编辑器使用 Playwright 键盘输入而不是 `fill()`；独立页和 modal 页的回复目标提示位置不同；已知引导遮罩和登录提示需要先通过 DOM 控件处理。

当前代码将这些经验收敛到 `DouyinPlaywrightProvider` 的 selector registry 和显式错误路径中。它不会隐藏验证码、绕过风控或把 DOM 结果宣称为完整评论；二级评论能力在没有稳定父子关联证据前保持关闭。

## 明确不实现的能力

验证码破解、绕过风控、设备指纹伪造、代理池规避检测、批量注册、批量骚扰私信、自动加微信、自动发送营销信息，以及任何绕过平台权限的行为都不在实现范围内。
