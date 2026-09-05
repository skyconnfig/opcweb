# 商业化基线与部署验收

当前版本的商业化边界是“单租户、小规模试用、纯文本 AI、人工确认跟进”。系统不发送私信、不绕过平台风控，也不分析图片、视频画面或截图。

## 运行模式

- 本地开发和真实试用默认使用 `CONTENT_PROVIDER=douyin-playwright`；系统不提供生产 Mock/Demo fallback。
- 真实试用必须在“抖音账号”页面打开可见浏览器，由用户人工登录并完成必要验证，再在数据源页面执行健康检查。
- 当前主链路只使用抖音 Playwright DOM Provider。旧的外部适配器仅保留为隔离代码，不在默认 Provider 注册表或生产扫描链路中启用。
- Provider 已兼容两类已观测页面形态：独立视频页可直接读取评论区，feed/modal 视频页会先处理非安全引导层并点击评论图标，再读取 `.comment-mainContent`；回复使用 Draft.js 键盘输入并校验回复目标。由于抖音 DOM 会变化，首次真实登录后仍必须执行现场 smoke test。
- LLM 只需要 OpenAI Compatible 文本接口：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。
- 生产部署必须设置 `API_AUTH_TOKEN` 和 `SETTINGS_ENCRYPTION_KEY`。后者使用 Fernet 密钥，不能使用普通字符串。

## Docker 验收

1. 复制 `.env.example` 为 `.env`。
2. 将 `DATABASE_URL` 中的数据库密码与 `POSTGRES_PASSWORD` 都替换为强密码，并设置 `API_AUTH_TOKEN`、`SETTINGS_ENCRYPTION_KEY`、LLM 配置和 Provider 配置。
3. 执行 `docker compose config`，确认变量已解析且没有明文 Key。
4. 执行 `docker compose up --build -d`。
5. 验证 `http://127.0.0.1:8689/health`、`http://127.0.0.1:8689/docs` 和 `http://127.0.0.1:5173`。

Web 容器使用 Next.js 服务端 API 代理。`API_AUTH_TOKEN` 仅作为 Web/API 容器运行时环境变量存在，不应配置为 `NEXT_PUBLIC_API_TOKEN`，也不应出现在客户端代码或构建参数中。API 的 `/ready` 探针会验证数据库连接，Compose healthcheck 和 CI smoke 均使用该探针。

API 使用 `Authorization: Bearer <API_AUTH_TOKEN>`。前端容器在构建时读取同一个 Token；更换 Token 后需要重新构建 web 服务。

## 自用验收顺序

1. 创建项目并运行 IndustryAgent、KeywordAgent。
2. 在数据源页健康检查并激活一个真实 Provider。
3. 用少量关键词执行扫描，确认视频、评论、覆盖状态和 DOM 来源标记都有真实返回；分页边界以页面实际 DOM 为准，不把未知覆盖范围标成完整。
4. 检查规则预筛后再调用 LeadJudgeAgent，并抽样人工复核潜客。
5. 生成 PersonaAgent 建议，人工确认后再在平台内操作。
6. 重启 API，确认 queued/running 任务会恢复，SSE 可以从持久化事件继续读取。

## 两个当前硬门槛

完整的逐步矩阵、人工操作和证据字段见 [`docs/real-acceptance-matrix.md`](real-acceptance-matrix.md)。以下状态必须以该矩阵为准：接口路径存在不代表真实验收通过。

### 1. douyin-comments-crawler 真实链路

外部 Provider 使用该服务的真实接口链路：`POST /api/collect/search`，轮询 `/api/collect/status/{task_id}`，从该任务状态响应的 `data` 字段读取本次任务的视频，再对每个真实视频 URL 调用 `POST /api/video/comments`。不再依赖共享的 `/api/data/videos`，避免并发任务串数据。验证命令：

```powershell
.\.venv\Scripts\python.exe scripts\crawler_smoke_test.py --keyword "长沙装修" --limit 2
```

健康检查失败、搜索任务失败、没有视频 URL 或评论接口失败都会中止；不会生成合成视频、合成评论或自动切换 Mock。

**当前状态：核心链路通过，证据包待补齐。** 2026-09-04 的真实复测报告 `data/acceptance/douyin-crawler-smoke-20260904-runtime-trace.json` 显示服务健康检查为 `connected`，关键词“长沙装修”返回 2 个真实视频 URL，并分别取得 3 条和 7 条带平台评论 ID 与文本的评论，覆盖状态为 `partial`，记录了真实任务 ID与轮询次数，未发送回复。仍需补齐系统入库关联、页面形态/selector 和人工复核字段，不能仅凭 smoke 报告宣称商业化验收完成。

### 2. 真实登录抖音 E2E

Playwright 验收必须由用户在可见浏览器中人工扫码并完成平台要求的验证，然后执行：

```powershell
.\.venv\Scripts\python.exe scripts\douyin_smoke_test.py --keyword "长沙装修" --limit 2
```

该脚本必须打印真实视频 URL、真实评论 ID/文本和 `coverage=partial`。未登录、需要验证、选择器变更或没有可解析内容均为失败，不使用假数据兜底。

**当前状态：核心链路已通过，证据包待补齐。** `data/acceptance/douyin-playwright-smoke-20260904-runtime-trace.json` 已记录真实账号 `LOGGED_IN`、关键词“长沙装修”、2 个真实视频 URL、5/9 条带真实评论 ID 与文本的评论、实际 DOM selector，覆盖状态为 `partial`，且 `reply_sent=false`。仍需补齐系统入库关联和人工复核字段；两条链路都完成证据包后，才可进入小规模真实试用。

### 两个门槛的上线判定

只有当上述两个门槛均为“通过”，并且各自证据包包含 `run_id`、版本、关键词、真实视频 URL/ID、真实评论 ID/文本、来源、时间、覆盖状态、人工复核人与失败记录时，才允许进入小规模真实试用。任一门槛为“未通过”或“部分通过”，商业化状态均保持“不可验收通过”。

## 定时扫描验收

- 在任务中心按项目启用自动扫描，频率可选 10～30 分钟；默认关闭。
- API 会将到期计划排入持久化任务队列；已有 queued/running 任务时跳过重复入队。
- 计划的 `next_run_at` / `last_run_at` 会写入数据库，API 重启后由 APScheduler 继续检查。
- SQLite 本地开发和 PostgreSQL/Docker 都使用 Alembic `upgrade head`；当前 head 为 `f4e5d6c7b8a9`，包含评论来源、回复恢复、采集任务来源和旧表空值规范化等迁移。

## 仍需独立完成的商业规模能力

真实多租户、计费订阅、团队 RBAC、独立 Redis/队列 Worker、平台授权审查、数据保留与删除策略、备份灾备、监控告警和标注集质量评估，不能用 Mock 链路替代，必须作为上线前的独立验收门槛。
