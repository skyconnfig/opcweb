import re

from app.agents.llm import input_hash


BUYING_TERMS = ["多少钱", "预算", "推荐", "靠谱", "联系", "怎么选", "准备", "需要", "能做吗", "报价", "增项", "翻新", "装修", "买", "哪里有"]
NOISE_TERMS = ["哈哈", "666", "好看", "主播好帅", "路过", "支持", "收藏了", "讲得很好"]


class RulePreFilter:
    def should_analyze(self, content: str, seen_hashes: set[str] | None = None) -> bool:
        text = re.sub(r"\s+", "", content or "")
        if len(text) < 4 or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
            return False
        if any(term == text or text.startswith(term) and len(text) <= len(term) + 2 for term in NOISE_TERMS):
            return False
        return True


class LeadJudgeAgent:
    prompt_version = "lead_judge_v1"

    async def run(self, project: dict, comment: dict) -> dict:
        content = comment.get("content", "")
        signals = [term for term in BUYING_TERMS if term in content]
        score = min(99, 45 + len(signals) * 9 + (12 if project.get("location") and project["location"] in content else 0) + (12 if re.search(r"\d+", content) else 0))
        if not signals:
            score = 26
        level = "S" if score >= 90 else "A" if score >= 75 else "B" if score >= 60 else "C"
        is_lead = score >= 60
        budget_match = re.search(r"(?:预算|(?:能|要))[^，。！？]{0,8}(\d+[万千]?)(?:元)?", content)
        return {"is_lead": is_lead, "confidence": round(min(.99, .65 + len(signals) * .06), 2), "lead_score": score, "lead_level": level, "intent_level": "high" if score >= 75 else "medium" if score >= 60 else "low", "need": project.get("service", project.get("industry", "")), "location": project.get("location", ""), "budget": budget_match.group(1) if budget_match else "", "time_requirement": "近期" if "准备" in content or "最近" in content else None, "purchase_stage": "comparison" if "推荐" in content or "怎么选" in content else "research", "pain_point": "担心增项与踩坑" if "坑" in content or "增项" in content else "", "buying_signals": signals, "summary": f"{comment.get('nickname', '该用户')}正在表达真实的{project.get('industry', '服务')}需求。" if is_lead else "当前评论更像泛互动。", "reason": "出现明确询价、地域、预算或联系方式信号。" if is_lead else "缺少明确的购买问题或需求上下文。", "recommended_action": "priority_follow_up" if level == "S" else "follow_up" if is_lead else "observe"}
