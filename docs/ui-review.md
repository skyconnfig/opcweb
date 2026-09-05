# AI 截流雷达：前端设计审查记录

日期：2026-09-04

## Critique

旧版前端更像功能验证页：信息层级偏平，导航、数据状态和实时工作感不够明确；视觉语言也缺少统一的色彩、间距和组件边界。对于“持续发现购买信号”的产品，用户最先需要看到的是运行状态、机会质量和可行动的潜客，而不是一个泛化的 SaaS 欢迎区。

本轮确定为 refined operations console：以暖灰工作区、墨黑文字和单一砖红 accent 建立识别度；用表格、事件流和紧凑指标承载密度；用衬线标题与清晰的无衬线正文形成层级。明确避免紫蓝渐变、玻璃拟态、卡片套卡片、巨型 Hero、过度圆角和过度动画。

## Audit

- 布局：桌面端固定侧边栏 + sticky 顶栏 + 可滚动工作区；主要页面采用 4/8px 节奏，内容宽度受控，数据表不被过度压缩。
- 视觉：背景、surface、border、accent、status 均使用 CSS 变量；主圆角集中在 8/10/14px，阴影保持在 1px + 极轻扩散。
- 交互：导航切换写入 hash；主题切换支持 light/dark；按钮具备 hover、press、focus-visible、disabled/loading；数据请求具备 loading skeleton、empty state 和 error banner。
- 响应式：1150px 收紧边距与网格，820px 切换移动导航和单列内容，520px 进一步压缩表单、指标与抽屉。
- 可观测性：总览接入 `/api/dashboard` 与 `/api/events/stream`；智能截流接入创建项目、smart-mode、scan；潜客抽屉接入 PersonaAgent 建议接口。
- 内容边界：页面明确显示 Douyin Playwright 真实 Provider、评论覆盖范围以 DOM 返回为准，以及 PersonaAgent 只生成建议、不自动发送。
- 字体：标题优先使用 Iowan Old Style / Baskerville / Palatino / Georgia，正文使用 Avenir Next、Segoe UI、苹方和微软雅黑回退，避免全站无脑使用 Inter。

## Fixes

1. 将 Vite 单页迁移为 Next.js App Router，保留后端 API 合同。
2. 将所有主要产品域收进统一壳层：总览、智能截流、关键词雷达、热门视频、潜客池、智能体、任务中心、数据分析、数据源和系统设置。
3. 把实时信号流、关键词机会分、潜客等级和 checkpoint 放到产品主路径中。
4. 为潜客详情加入 AI 摘要、购买信号、评分信息和人工跟进建议抽屉。
5. 修复浏览器通过 `localhost:5173` 访问时固定请求 `127.0.0.1:8688` 引发的 `Failed to fetch`：未配置环境变量时，API host 现在跟随当前浏览器 hostname。
6. 补充 `@types/node`，使 Next.js 的环境变量读取通过类型检查。

## Polish

- 砖红只用于主操作、机会分、选中导航和关键状态，绿色只表达在线/完成，避免多 accent 竞争。
- 表格、事件流和指标卡使用细边框与微弱层次，减少无意义装饰。
- 移动端使用抽屉式侧栏、横向滚动数据表和单列内容，保证功能继续可用。
- 交互验证覆盖智能截流表单、潜客详情抽屉、PersonaAgent 建议生成和全部页面路由。

## Verification

- `.venv\\Scripts\\python.exe -m compileall -q backend tests`：通过
- `.venv\\Scripts\\python.exe -m pytest -q`：5 passed
- `npm run lint`：通过
- `npm run build`：通过
- `http://127.0.0.1:8689/health`：应返回 `provider=Douyin Playwright`，并明确反映 LLM/登录状态
- `http://localhost:5173/`：HTTP 200，空数据库显示真实空状态
- 项目、智能模式和扫描接口：仅在用户已配置文本模型并登录真实抖音后执行
- `GET /api/events/stream`：SSE 返回 `connected`

开发期面板曾显示一条 hydration 提示，差异来自 Chrome 扩展注入的 `crxlauncher`、Youdao 等 `<html>` 属性；应用自身浏览器 error/warn 日志为空，生产构建不受影响。
