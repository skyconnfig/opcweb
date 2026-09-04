# 商业化基线与部署验收

当前版本的商业化边界是“单租户、小规模试用、纯文本 AI、人工确认跟进”。系统不发送私信、不绕过平台风控，也不读取图片或视频画面。

## 运行模式

- 本地开发默认 `CONTENT_PROVIDER=mock`，用于验证产品流程。
- 真实试用必须选择已完成合规配置的外部 Provider，并在数据源页面执行健康检查。
- Douyin Comments Crawler 若只返回关键词级评论集合，适配器会按“关键词评论集合”保存并继续分析，不会伪造视频 URL；若返回视频 URL，则按视频逐条抓取评论。
- LLM 只需要 OpenAI Compatible 文本接口：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。
- 生产部署必须设置 `API_AUTH_TOKEN` 和 `SETTINGS_ENCRYPTION_KEY`。后者使用 Fernet 密钥，不能使用普通字符串。

## Docker 验收

1. 复制 `.env.example` 为 `.env`。
2. 设置 `API_AUTH_TOKEN`、`SETTINGS_ENCRYPTION_KEY`、LLM 配置和 Provider 配置。
3. 执行 `docker compose config`，确认变量已解析且没有明文 Key。
4. 执行 `docker compose up --build -d`。
5. 验证 `http://127.0.0.1:8689/health`、`http://127.0.0.1:8689/docs` 和 `http://127.0.0.1:5173`。

API 使用 `Authorization: Bearer <API_AUTH_TOKEN>`。前端容器在构建时读取同一个 Token；更换 Token 后需要重新构建 web 服务。

## 自用验收顺序

1. 创建项目并运行 IndustryAgent、KeywordAgent。
2. 在数据源页健康检查并激活一个真实 Provider。
3. 用少量关键词执行扫描，确认视频、评论、覆盖状态和 cursor 都有真实返回。
4. 检查规则预筛后再调用 LeadJudgeAgent，并抽样人工复核潜客。
5. 生成 PersonaAgent 建议，人工确认后再在平台内操作。
6. 重启 API，确认 queued/running 任务会恢复，SSE 可以从持久化事件继续读取。

## 定时扫描验收

- 在系统设置中按项目启用自动扫描，频率可选 15 分钟至 7 天。
- API 会将到期计划排入持久化任务队列；已有 queued/running 任务时跳过重复入队。
- 计划的 `next_run_at` / `last_run_at` 会写入数据库，API 重启后由 APScheduler 继续检查。
- SQLite 本地开发由 `create_all()` 自动补表；PostgreSQL/Docker 使用 Alembic `upgrade head`，当前 head 为 `7f1a2c9d4e6b`。

## 仍需独立完成的商业规模能力

真实多租户、计费订阅、团队 RBAC、独立 Redis/队列 Worker、平台授权审查、数据保留与删除策略、备份灾备、监控告警和标注集质量评估，不能用 Mock 链路替代，必须作为上线前的独立验收门槛。
