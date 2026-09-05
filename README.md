# AI 截流雷达 / AI Lead Radar

一个 Windows 优先、Provider 可替换的行业获客雷达 MVP。输入行业、地区、业务与目标客户后，系统会通过文本模型生成行业关键词，依据公开视频元数据与公开评论识别购买信号，汇总潜客并生成仅供人工参考的跟进建议。

第一版只使用文本模型，不接入任何视觉模型。架构边界见 [`docs/text-only-architecture.md`](docs/text-only-architecture.md)。

## 快速启动

PowerShell：

```powershell
.\start.ps1
```

或分开启动：

```powershell
uv venv .venv --python 3.13
uv pip install -e ".[test]" --python .venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --loop app.uvicorn_loop:create_loop --port 8689
cd web
npm install
npm run dev
```

如需启用独立的 `douyin-comments-crawler` 服务，请在另一个 PowerShell 窗口运行：

```powershell
.\scripts\start_crawler.ps1
```

主服务和可选 crawler 默认共用 `data/browser/douyin` 持久化 Profile，关闭并重新启动服务不会清除登录态，也不需要重复登录。由于 Playwright 与 DrissionPage 不能同时占用同一个 Chromium Profile，启动 crawler 前请先在“抖音账号”页面关闭主 API 的抖音浏览器；脚本会检测占用并拒绝并发启动。只有设置 `DOUYIN_CRAWLER_PROFILE_DIR` 时才会使用独立 Profile，此时需要单独登录。

打开 http://127.0.0.1:5173。API 文档在 http://127.0.0.1:8689/docs。

### 10～30 分钟自动评论采集

进入“任务中心 → 自动采集”，选择 10、15、20、25 或 30 分钟并保存计划。首次采集会在保存后的设定间隔到期后执行，之后按同一间隔循环；调度器每分钟检查一次到期计划，同一项目已有任务运行时不会重复入队。每次新扫描都会重新请求公开评论，不复用上一次扫描的 crawler 缓存。勾选“扫描全部启用关键词”可覆盖全部关键词，否则每轮按机会排序处理前 8 个关键词。

计划、任务状态、下次采集时间和扫描报告都持久化在数据库中；API 重启后会继续执行已启用的计划。评论采集仍只使用公开文本和结构化字段，不读取视频画面。

前端通过 Next.js 服务端代理访问 API；`API_AUTH_TOKEN` 只在服务端环境中使用，不会作为 `NEXT_PUBLIC_*` 变量打进浏览器 bundle。生产 Compose 中 Web 容器通过 `API_URL=http://api:8689` 访问 API。

生产部署前请在 `.env` 中设置 `API_AUTH_TOKEN` 和 `SETTINGS_ENCRYPTION_KEY`。可用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成加密密钥。

系统不预置项目、视频、评论或潜客，也不会使用 Mock Provider。所有扫描数据来自用户登录后的真实抖音 DOM；业务 Agent 只使用 OpenAI Compatible 文本模型。未配置 LLM、未登录、需要人工安全验证或页面无法解析时会返回明确错误。

## Windows 本地运行

```powershell
uv venv .venv
uv pip install -e ".[test]"
.venv\Scripts\playwright.exe install chromium
Copy-Item .env.example .env
.\start.ps1
```

在系统设置中配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。首次使用进入“抖音账号”打开真实浏览器并扫码登录；之后服务会自动复用 `data/browser/douyin` 中的持久化登录态。扫描只读取标题、description、作者、互动数据和公开评论文本；回复默认人工审核，点击确认后才会真实发送并重新读取 DOM 验证。只有用户在项目回复策略中主动开启“自动回复模式”，并且意图、置信度、潜客分数、知识库、敏感风险和限速规则全部通过时，系统才会自动调用同一真实 Playwright 回复链路；不满足条件的内容仍进入待审核队列。

部署探针：`/health` 用于存活检查，`/ready` 会额外验证数据库连接。生产环境应以 `/ready` 作为容器编排的就绪条件。

## 目录

- `backend/app`：FastAPI、SQLAlchemy、Agent、Provider、任务引擎
- `web`：React + TypeScript + Next.js App Router + Tailwind CSS 4
- `docs/open-source-research.md`：四个参考项目的取证与边界
- `tests`：Provider、规则预筛、评分、去重、checkpoint、SSE 等测试
- `start.ps1` / `start.bat`：Windows 开发启动脚本

## 数据与合规边界

主系统只处理公开内容、机会判断、潜客 CRM 和人工跟进辅助。不实现验证码破解、绕过风控、批量注册、设备指纹伪造、批量骚扰私信、自动加微信或自动营销发送。外部爬虫必须由用户独立运行并自行确认平台规则与许可证。

更多配置见 `.env.example` 与 `THIRD_PARTY.md`。
