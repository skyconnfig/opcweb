import json

from app.agents.llm import BaseLLMProvider, LLMCall


class PersonaAgent:
    prompt_version = "persona_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, project: dict, lead: dict, persona: dict, comments: list[str] | None = None) -> dict:
        comments = comments or []
        if self.llm and self.llm.configured:
            result = await self.llm.structured_output(
                "你是人设跟进 Agent。只使用项目文字、人设文字、潜客字段和公开评论文本。只输出供人工确认的建议，不自动发送，不索取联系方式。严格返回 JSON。",
                json.dumps({"project": project, "lead": lead, "persona": persona, "comment_history": comments}, ensure_ascii=False),
                {"type": "object"},
            )
            if result:
                return self._normalize(result, project, lead)
        location = project.get("location", "本地")
        need = lead.get("need") or project.get("service") or project.get("industry")
        budget = lead.get("budget")
        budget_text = f"，你提到的预算是{budget}" if budget else ""
        reply = f"{location}这类{need}的价格差距确实会比较大{budget_text}，主要要先看具体需求和范围。建议先把面积/现状、想做的部分和预算区间对齐，不然后面很容易出现增项。"
        fallback = {"customer_insight": lead.get("summary", "客户正在了解方案"), "communication_strategy": "先回答具体问题，再补一个低压力的澄清问题，不主动索要联系方式。", "recommended_reply": reply, "follow_up_question": "你现在更想先了解预算，还是已经有户型/现场信息了？", "warnings": ["仅供人工参考", "不要自动发送", "避免虚假承诺、最低价和夸大效果"]}
        if self.llm:
            self.llm.last_call = LLMCall("deterministic-mock", json.dumps({"project": project, "lead": lead, "persona": persona, "comment_history": comments}, ensure_ascii=False), fallback)
        return fallback

    @staticmethod
    def _normalize(result: dict, project: dict, lead: dict) -> dict:
        fallback = {"customer_insight": lead.get("summary", "客户正在了解方案"), "communication_strategy": "先回答具体问题，再补一个低压力的澄清问题。", "recommended_reply": "建议先围绕客户的具体问题给出清晰、克制的解释。", "follow_up_question": "你现在更想先了解预算，还是已经有户型/现场信息了？", "warnings": ["仅供人工参考", "不要自动发送"]}
        return {**fallback, **result}
