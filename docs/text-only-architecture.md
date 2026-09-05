# 文本模型架构边界

本项目第一版只使用文本模型，不接入任何视觉模型。抖音采集与 AI 判断只允许使用文字、结构化字段、公开视频元数据、视频标题、description、作者昵称、公开评论、二级评论、客户历史评论、用户人设和知识库文本。

禁止图片理解、视频画面理解、OCR、视频帧分析、封面视觉判断、多模态模型、Vision API、图像 embedding 和视频内容识别。封面或图片 URL 如存在，只作为来源字段保存，不进入模型调用。

## Agent 边界

- IndustryAgent：行业、地区、业务、目标客户、客单价和描述 → 行业摘要、客户画像、痛点、购买触发点、常见问题、客户语言、竞品类型和搜索策略。
- KeywordAgent：行业文本与客户语言 → 100–300 个分类型关键词和机会分。
- RadarAgent：title、description、keyword、author、发布时间和公开互动数据 → 行业相关度、商业相关度、潜客机会分和等级。
- LeadJudgeAgent：项目上下文、来源关键词、视频文字字段、当前评论、二级评论和同一用户历史评论 → 潜客结构化判断。
- PersonaAgent / ReplyAgent：项目、人设、潜客字段、评论文本和知识库 → 仅供人工确认的跟进建议或回复草稿。

## Provider 边界

代码只提供 `BaseLLMProvider` 与 `OpenAICompatibleProvider`，只发送字符串消息到 `/chat/completions`。支持 DeepSeek、Qwen、GPT 及其他 OpenAI Compatible 文本模型。只需配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，另有温度和超时配置。

未配置或调用失败时返回明确的 `LLM_NOT_CONFIGURED` / `LLM_REQUEST_FAILED` / `LLM_INVALID_RESPONSE`，不生成 Mock、Demo 或合成结果。

## 成本与审计

评论先经过 `RulePreFilter`，过滤短文本、纯表情、重复和明显泛互动，再进入文本模型。每次 Agent 运行记录 Agent 名称、模型、prompt 版本、输入文本、JSON 输出、token、耗时、成功状态和错误信息；不记录图片字节，也没有视觉模型调用路径。

## 验收链路

只配置三个必需变量即可运行完整文本链路：

行业信息 → 文本行业理解 → 100–300 个关键词 → 文本元数据机会评分 → 公开评论 → 文本潜客判断 → 潜客池 → 文本人设跟进建议。
