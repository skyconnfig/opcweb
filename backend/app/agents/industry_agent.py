import json

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


class IndustryAgent:
    prompt_version = "industry_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, context: dict) -> dict:
        if self.llm is None:
            raise LLMNotConfiguredError("IndustryAgent 需要已配置的文本模型")
        system = """你是行业研究 Agent，只能处理用户提供的文字和结构化字段，绝不使用图片、视频画面、OCR 或任何视觉能力。
只输出一个 JSON 对象，字段名必须完全使用下面 8 个名称，不得改名、嵌套或用 analysis/recommendations 等替代字段：
{"industry_summary":"行业与业务摘要","target_customer_profiles":["目标客户画像"],"pain_points":["客户痛点"],"buying_triggers":["购买触发点"],"common_questions":["客户常见问题"],"customer_language":["客户会使用的原话或短语"],"competitor_types":["竞品类型"],"search_strategy":["文本搜索策略"]}
所有字段都必须存在；列表字段至少返回 1 项；信息不足时只能根据输入文字做保守归纳，不要编造外部事实。""",
        payload = json.dumps(self._text_context(context), ensure_ascii=False)
        last_error = None
        previous_output = {}
        for attempt in range(2):
            try:
                request_payload = payload if attempt == 0 else json.dumps(
                    {
                        "input": self._text_context(context),
                        "previous_output": previous_output,
                        "repair_instruction": "把 previous_output 的语义映射到要求的 8 个字段；缺失项只能从 input 保守归纳。",
                    },
                    ensure_ascii=False,
                )
                result = await self.llm.structured_output(
                    system if attempt == 0 else f"{system}\n上一次输出不符合字段契约。再次检查每个字段名，并只返回完整 JSON。",
                    request_payload,
                    {"type": "object"},
                )
                return self._normalize(result, context)
            except LLMInvalidResponseError as exc:
                last_error = exc
                previous_output = result if "result" in locals() and isinstance(result, dict) else {}
                if self.llm.last_call is not None:
                    self.llm.last_call.success = False
                    self.llm.last_call.error = str(exc)
        raise last_error

    @staticmethod
    def _text_context(context: dict) -> dict:
        return {key: str(context.get(key, "")) for key in ("industry", "location", "service", "target_customer", "price_range", "description")}

    def _normalize(self, result: dict, context: dict) -> dict:
        required = ("industry_summary", "target_customer_profiles", "pain_points", "buying_triggers", "common_questions", "customer_language", "competitor_types", "search_strategy")
        missing = [key for key in required if key not in result]
        if missing:
            raise LLMInvalidResponseError(f"IndustryAgent 缺少字段: {', '.join(missing)}")
        return result
