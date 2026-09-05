# 两个硬门槛真实验收矩阵

本文只判断第一版能否进入真实试用，不等同于单元测试、接口联通或前端页面验收。任何一项“未通过”都不能标记为“可真实运行”，也不能用 Mock、Demo、fixture 或合成数据补齐结果。

## 当前结论（2026-09-04）

| 硬门槛 | 当前状态 | 已知证据 | 尚缺证据 |
| --- | --- | --- | --- |
| `douyin-comments-crawler`：关键词 → 视频 → 评论 | 核心链路通过，证据包待补齐 | `data/acceptance/douyin-crawler-smoke-20260904-runtime-trace.json`：健康检查 `connected`，关键词“长沙装修”返回 2 个真实视频 URL，并取得 3/7 条带真实评论 ID 与文本的评论，`coverage=partial`，记录真实任务 ID、轮询次数、未发送回复 | 补齐系统入库关联、页面形态/selector 和人工复核字段 |
| Playwright：真实登录 → 搜索 → 评论 | 核心链路通过，证据包待补齐 | `data/acceptance/douyin-playwright-smoke-20260904-runtime-trace.json`：`LOGGED_IN`、2 个真实视频 URL、5/9 条带真实评论 ID 与文本的评论、实际 DOM selector、`coverage=partial`、`reply_sent=false` | 补齐系统入库关联和人工复核字段 |

“服务层可连接”与“真实链路通过”是不同状态。当前不能据此宣称商业化验收完成。

## 统一判定规则

- **通过**：所有必填步骤完成，证据字段齐全，结果可由真实响应或真实浏览器 DOM 重放核对。
- **部分通过**：链路中已有真实结果，但缺少任一必填证据、覆盖范围不明、或仅完成接口层检查；不得作为上线通过。
- **未通过**：登录失败、搜索失败、无可解析视频、评论失败、selector 失效、返回空结果且无法证明是平台真实空结果，或出现任何假数据兜底。
- 评论覆盖状态必须记录为 `partial`，除非有独立、可核验的完整性证明；不得把分页结束、超时或空页当作完整评论。
- 失败必须暴露为明确错误并保留上下文；不得自动切换到 Mock/Demo、fixture、硬编码样例或合成视频/评论。

## 门槛一：`douyin-comments-crawler` 真实关键词 → 视频 → 评论

### 验收矩阵

| 阶段 | 操作与前置条件 | 必须观察到的真实结果 | 必填证据字段 | 失败判定 |
| --- | --- | --- | --- | --- |
| 0. 服务健康 | 启动 crawler，检查 `/health`；确认其运行所需的登录/浏览器状态 | 健康检查成功，服务版本/时间可记录 | `checked_at`, `base_url`, `health_status`, `health_response_redacted` | 服务不可达、健康状态非成功 |
| 1. 创建搜索任务 | 以固定 `keyword` 调用 `POST /api/collect/search` | 返回真实 `task_id`，请求参数与时间可追溯 | `keyword`, `search_request_id`, `task_id`, `requested_at` | 无任务 ID、请求被降级或使用样例响应 |
| 2. 轮询任务 | 轮询 `/api/collect/status/{task_id}` 直到成功/失败/明确终态 | 记录终态、耗时和服务返回的真实计数 | `task_status`, `status_checked_at`, `poll_count`, `duration_ms`, `video_count_reported` | 超时、异常终态、无法关联任务 |
| 3. 读取视频 | 从 `/api/collect/status/{task_id}` 的任务级 `data` 字段读取本次任务视频 | 至少 1 条真实视频，含可打开的 `http(s)` URL；记录标题或公开元数据 | `video_id`, `video_url_redacted_or_hash`, `title`, `description_present`, `author`, `publish_time`, `like_count`, `comment_count`, `share_count`, `collect_count` | 0 条、URL 不可用、无法证明属于本次关键词任务 |
| 4. 获取评论 | 对真实视频 URL 调用 `POST /api/video/comments` | 至少 1 条真实评论，含稳定评论 ID 和文本；不得只返回计数 | `comment_id`, `comment_text`, `parent_comment_id_or_null`, `comment_source`, `comment_fetched_at`, `coverage` | 评论为空且无平台真实空结果证据、无 ID/文本、评论接口失败 |
| 5. 写入与关联 | 检查系统入库记录和任务报告 | 视频、评论、关键词、任务之间可关联；来源标记为 crawler | `project_id`, `task_id`, `keyword_id`, `video_record_id`, `comment_record_id`, `provider`, `source_url`, `created_at` | 只能在日志中看到结果，无法在系统内关联 |

### 人工步骤

1. 在 crawler 所使用的可见浏览器中人工完成抖音登录及平台要求的验证；不代办验证码，不绕过风控。
2. 固定一个验收关键词，例如“长沙装修”，记录关键词、时区和开始时间。
3. 运行 crawler smoke 流程，等待任务进入明确终态；建议追加 `--report data\acceptance\douyin-crawler-smoke-YYYYMMDD.json` 自动保存脱敏证据，不要手工填充返回 JSON。
4. 从返回结果中打开至少一个真实视频 URL，人工核对标题/作者与返回记录一致。
5. 人工核对至少一条评论的页面文本与 `comment_id`，再核对系统入库内容。
6. 保存脱敏报告；API Key、Cookie、完整登录态、Authorization header 不得进入报告或仓库。

## 门槛二：Playwright 真实登录 → 搜索 → 评论

### 当前现场结果

已取得一份真实现场 smoke 报告：`data/acceptance/douyin-playwright-smoke-20260904-runtime-trace.json`。报告记录了持久化 Profile、`login_state=LOGGED_IN`、关键词“长沙装修”、2 个真实视频 URL，以及分别为 5 条和 9 条的真实评论样本；评论带平台 ID 和文本，覆盖状态为 `partial`，并记录了实际 DOM selector，没有发送回复。该结果证明核心“真实登录 → 搜索 → 评论”链路已跑通，但尚未替代下方证据包的全部字段要求。

### 验收矩阵

| 阶段 | 操作与前置条件 | 必须观察到的真实结果 | 必填证据字段 | 失败判定 |
| --- | --- | --- | --- | --- |
| 0. 浏览器准备 | 以 `douyin-playwright` 启动持久化 Profile 和可见浏览器 | 浏览器进程、Profile 路径（脱敏）和页面 URL 可记录 | `browser_started_at`, `profile_id_or_hash`, `page_url`, `browser_context_id` | 隐藏浏览器、Profile 不可写或页面未打开 |
| 1. 真实登录 | 用户人工扫码/登录并完成平台验证 | 页面显示已登录状态，登录状态检查为成功 | `login_method`, `login_verified_at`, `login_state`, `verification_required` | 未登录、登录过期、需验证未完成 |
| 2. 搜索关键词 | 在真实抖音页面提交固定关键词 | DOM 中出现真实搜索结果，并解析至少 1 个视频 URL | `keyword`, `search_started_at`, `search_completed_at`, `result_count_dom`, `video_url`, `title`, `author` | 无结果、只能生成 URL、结果不是页面 DOM |
| 3. 打开视频/评论区 | 处理独立视频页或 feed/modal 页面 | 真实视频页打开；评论区可见。modal 必要时点击 `[data-e2e="feed-comment-icon"]` | `page_shape`, `comment_open_action`, `comment_container_selector`, `opened_at` | 评论区未打开、只依赖截图/视觉判断、selector 失败 |
| 4. 读取评论 | 在 `.comment-mainContent` 等真实 DOM 容器内滚动/分页 | 至少 1 条评论有真实文本与稳定 ID；记录 `coverage=partial` | `comment_id`, `comment_text`, `parent_comment_id_or_null`, `comment_item_selector`, `scroll_batches`, `coverage`, `fetched_at` | 无评论文本/ID、把未知覆盖标完整、使用假评论 |
| 5. 系统同步 | 检查视频/评论是否写入项目并触发后续规则预筛 | 记录能回溯到页面、关键词和 provider；后续 LLM 输入只含文本/结构化字段 | `project_id`, `provider`, `source_url`, `video_record_id`, `comment_record_id`, `prefilter_result`, `agent_run_id` | 入库脱离来源、调用视觉模型或无审计记录 |

### 人工步骤

1. 打开系统“抖音账号”页面，启动可见 Playwright 浏览器。
2. 在浏览器内人工扫码登录并完成所有平台要求的验证；不要提供或记录 Cookie、密码、验证码。
3. 在数据源页面执行 Provider 健康检查并激活 `douyin-playwright`。
4. 使用固定关键词运行真实 smoke 流程，例如：

   ```powershell
   .\.venv\Scripts\python.exe scripts\douyin_smoke_test.py --keyword "长沙装修" --limit 2 --report data\acceptance\douyin-playwright-smoke-YYYYMMDD.json
   ```

5. 人工打开 smoke 输出中的真实视频 URL，核对页面标题、作者和至少一条评论文本/ID。
6. 检查系统入库记录、`coverage=partial`、来源标记和失败信息；核心结果已存在，但未补齐证据包字段前，状态保持“核心链路通过，证据包待补齐”。

## 证据包最低要求

每次验收应生成一个脱敏证据包，至少包括：

| 证据 | 要求 |
| --- | --- |
| 运行身份 | `run_id`, `operator`, `started_at`, `finished_at`, `timezone`, `environment` |
| 版本 | `git_commit_or_worktree_state`, `provider_version`, `browser_version`, `crawler_version` |
| 输入 | 原始 `keyword`、项目/数据源标识、请求时间；凭据只记录存在与否，不记录值 |
| 任务链路 | `task_id`、每个状态时间、请求/响应 HTTP status、错误码和脱敏响应摘要 |
| 视频证据 | 真实 `video_url`（可用 hash 脱敏）、`video_id`、标题、作者、公开互动字段、来源 provider |
| 评论证据 | `comment_id`、原文文本、`parent_comment_id`（无则 `null`）、抓取时间、`coverage`、DOM/API 来源 |
| 页面证据 | 页面 URL、页面形态、关键 selector/动作、必要时 HTML/截图快照路径；快照不得包含凭据 |
| 系统证据 | 入库 ID、任务报告、规则预筛结果、`agent_run_id`；LLM 仅记录文本输入/JSON 输出审计字段 |
| 判定 | 每个矩阵步骤的 `pass/fail`、失败原因、人工复核人、复核时间和最终结论 |

截图或 HTML 只能作为辅助证据，不能替代真实 API/DOM 记录，也不能用于推断视频画面内容。任何图片 URL 如被保存，只能作为 URL 字段保存，第一版不做 OCR、视觉模型、视频帧分析或图像识别。

## 不得作为通过证据的内容

- Mock/Demo Provider、fixture、录制响应、硬编码视频/评论、空结果自动填充。
- “接口返回 200”但没有真实任务、视频 URL、评论 ID/文本的报告。
- 只有评论数量、截图、封面或视频帧，没有文本评论内容的报告。
- 未登录状态下的前端展示、静态页面、组件截图或 lint/build 结果。
- 仅能证明规则预筛或 LLM 可调用，不能证明上游真实采集链路的结果。
