# 第一版文本模型架构约束

状态：生效

AI Lead Radar 第一版只使用文本模型。所有 Agent 的输入限定为项目文字、业务结构化字段、公开视频元数据、视频标题与 description、作者昵称、公开评论、二级评论文本、客户历史评论、人设配置和知识库文本。采集器可以保存封面或截图 URL，但业务 Agent 不读取这些 URL，也不进行画面推理。

## Agent 边界

- `IndustryAgent`：行业、地区、业务、目标客户、客单价和描述 → 行业摘要、画像、痛点、触发点、客户语言和搜索策略。
- `KeywordAgent`：行业文本与客户语言 → 100–300 个分类型关键词和机会分。
- `RadarAgent`：title、description、keyword、author、发布时间和公开互动数据 → 行业相关度、商业相关度、潜客机会分和等级。
- `LeadJudgeAgent`：项目上下文、来源关键词、视频文字字段、当前评论、二级评论和同一用户历史评论 → 潜客结构化判断。
- `PersonaAgent`：项目、人设、潜客字段和评论文本 → 仅供人工确认的跟进建议。

## Provider 边界

代码只提供 `BaseLLMProvider` 与 `OpenAICompatibleProvider` 两个文本调用接口。配置项为 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，可选 `LLM_TEMPERATURE` 和 `LLM_TIMEOUT`。未配置时使用确定性的本地 Mock fallback；配置后所有模型调用通过 `/chat/completions`，消息内容为字符串，返回严格 JSON。

## 成本与审计

评论先经过 `RulePreFilter`，过滤短文本、纯表情、重复和明显泛互动，再进入文本模型。每次 Agent 运行记录 Agent 名称、模型、prompt 版本、输入文本哈希与正文、JSON 输出、token、耗时、成功状态和错误信息，不记录任何图片输入。

## 验收链路

在只配置三个必需环境变量的情况下，链路为：行业信息 → 文本行业理解 → 100–300 个关键词 → 文本元数据机会评分 → 公开评论 → 文本潜客判断 → 潜客池 → 文本人设跟进建议。
