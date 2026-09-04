class PersonaAgent:
    prompt_version = "persona_v1"

    async def run(self, project: dict, lead: dict, persona: dict) -> dict:
        location = project.get("location", "本地")
        need = lead.get("need") or project.get("service") or project.get("industry")
        budget = lead.get("budget")
        budget_text = f"，你提到的预算是{budget}" if budget else ""
        reply = f"{location}这类{need}的价格差距确实会比较大{budget_text}，主要要先看具体需求和范围。建议先把面积/现状、想做的部分和预算区间对齐，不然后面很容易出现增项。"
        return {"customer_insight": lead.get("summary", "客户正在了解方案"), "communication_strategy": "先回答具体问题，再补一个低压力的澄清问题，不主动索要联系方式。", "recommended_reply": reply, "follow_up_question": "你现在更想先了解预算，还是已经有户型/现场信息了？", "warnings": ["仅供人工参考", "不要自动发送", "避免虚假承诺、最低价和夸大效果"]}

