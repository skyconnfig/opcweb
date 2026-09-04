import json

from app.agents.llm import BaseLLMProvider


class IndustryAgent:
    prompt_version = "industry_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, context: dict) -> dict:
        if self.llm and self.llm.configured:
            result = await self.llm.structured_output(
                "你是行业研究 Agent。只根据用户提供的文字和结构化字段工作，不使用任何媒体内容理解。严格返回 JSON。",
                json.dumps(self._text_context(context), ensure_ascii=False),
                {"type": "object"},
            )
            if result:
                return self._normalize(result, context)
        return self._fallback(context)

    @staticmethod
    def _text_context(context: dict) -> dict:
        return {key: str(context.get(key, "")) for key in ("industry", "location", "service", "target_customer", "price_range", "description")}

    def _normalize(self, result: dict, context: dict) -> dict:
        fallback = self._fallback(context)
        return {key: result.get(key, fallback[key]) for key in fallback}

    @staticmethod
    def _fallback(context: dict) -> dict:
        industry = context.get("industry", "目标行业")
        location = context.get("location", "本地")
        target = context.get("target_customer", "有明确需求的客户")
        return {
            "industry_summary": f"{location}{industry}服务，重点关注正在比较方案、询价或准备近期决策的人群。",
            "target_customer_profiles": [target],
            "target_customer_profile": target,
            "pain_points": ["信息不透明", "担心踩坑和增项", "不知道预算是否合理"],
            "buying_triggers": ["明确询价", "描述户型/规模", "询问本地能否服务", "表达近期计划"],
            "common_questions": ["多少钱", "哪家靠谱", "有没有推荐", "怎么收费", "值不值得", "能做吗"],
            "competitor_types": ["本地服务商", "连锁品牌", "个人工作室"],
            "customer_language": ["多少钱", "哪家靠谱", "有没有推荐", "怎么收费", "踩坑", "增项", "预算"],
            "search_strategy": [f"{location}{industry}", f"{location}{industry}多少钱", f"{industry}避坑", f"{target}怎么选"],
        }
