# AI 截流雷达 / AI Lead Radar

一个 Windows 优先、Provider 可替换的行业获客雷达 MVP。输入行业、地区、业务与目标客户后，系统会通过文本模型生成行业关键词，依据公开视频元数据与公开评论识别购买信号，汇总潜客并生成仅供人工参考的跟进建议。

第一版明确只使用文本模型，不接收图片或视频画面输入。Agent 只处理文字、结构化字段、公开视频元数据、公开评论、用户历史评论、人设和知识库文本。架构细则见 [`docs/text-only-architecture.md`](docs/text-only-architecture.md)。

## 快速启动

PowerShell：

```powershell
.\start.ps1
```

或分开启动：

```powershell
uv venv .venv --python 3.13
uv pip install -e ".[test]" --python .venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8689
cd web
npm install
npm run dev
```

打开 http://127.0.0.1:5173。API 文档在 http://127.0.0.1:8689/docs。

前端通过 Next.js 服务端代理访问 API；`API_AUTH_TOKEN` 只在服务端环境中使用，不会作为 `NEXT_PUBLIC_*` 变量打进浏览器 bundle。生产 Compose 中 Web 容器通过 `API_URL=http://api:8689` 访问 API。

生产部署前请在 `.env` 中设置 `API_AUTH_TOKEN` 和 `SETTINGS_ENCRYPTION_KEY`。可用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成加密密钥。

默认 Demo：长沙装修，包含 50 个关键词、20 个视频、300 条评论和一批可跟进潜客。未配置 LLM 时使用确定性的 Mock AI；未连接外部 Provider 时不会偷偷伪装成真实数据，界面会明确显示 Demo 数据模式。

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
