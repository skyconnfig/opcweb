import json
import re

from app.agents.llm import BaseLLMProvider


BUYING_TERMS = ["多少钱", "预算", "推荐", "靠谱", "联系", "怎么选", "准备", "需要", "能做吗", "报价", "增项", "翻新", "装修", "买", "哪里有"]
NOISE_TERMS = ["哈哈", "666", "好看", "主播好帅", "路过", "支持", "收藏了", "讲得很好", "学到了"]


class RulePreFilter:
    def should_analyze(self, content: str, seen_hashes: set[str] | None = None) -> bool:
        text = re.sub(r"\s+", "", content or "")
        if len(text) < 4 or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
            return False
        if any(term == text or (text.startswith(term) and len(text) <= len(term) + 2) for term in NOISE_TERMS):
            return False
        if seen_hashes is not None:
            import hashlib
            content_hash = hashlib.sha256(text.lower().encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                return False
            seen_hashes.add(content_hash)
        return True


class LeadJudgeAgent:
    prompt_version = "lead_judge_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, project: dict, comment: dict) -> dict:
        if self.llm and self.llm.configured:
            result = await self.llm.structured_output(
                "你是潜客判断 Agent。只分析文字评论、评论上下文、来源视频文字字段和公开互动数据，不使用任何画面。严格返回 JSON，并保留 is_lead、lead_score、intent_level、need、location、budget、time_requirement、purchase_stage、pain_point、buying_signals、summary、reason、recommended_action。",
                json.dumps({"project": self._text_context(project), "comment": comment}, ensure_ascii=False),
                {"type": "object"},
            )
            if result:
                return self._normalize(result, project, comment)
        return self._fallback(project, comment)

    def _normalize(self, result: dict, project: dict, comment: dict) -> dict:
        fallback = self._fallback(project, comment)
        result = {**fallback, **result}
        result["lead_score"] = max(0, min(100, float(result.get("lead_score", 0))))
        result["lead_level"] = "S" if result["lead_score"] >= 90 else "A" if result["lead_score"] >= 75 else "B" if result["lead_score"] >= 60 else "C"
        result["is_lead"] = bool(result.get("is_lead"))
        return result

    @staticmethod
    def _text_context(project: dict) -> dict:
        return {key: str(project.get(key, "")) for key in ("industry", "location", "service", "target_customer", "price_range", "description", "keyword", "video_title", "video_description", "video_creator", "video_likes", "video_comments", "video_shares", "video_collects", "history_text")}

    @staticmethod
    def _fallback(project: dict, comment: dict) -> dict:
        content = str(comment.get("content", ""))
        history = str(comment.get("history_text", ""))
        combined = f"{content}\n{history}"
        signals = [term for term in BUYING_TERMS if term in combined]
        score = min(99, 45 + len(set(signals)) * 9 + (12 if project.get("location") and project["location"] in combined else 0) + (12 if re.search(r"\d+", combined) else 0))
        if not signals:
            score = 26
        level = "S" if score >= 90 else "A" if score >= 75 else "B" if score >= 60 else "C"
        is_lead = score >= 60
        budget_match = re.search(r"(?:预算|(?:能|要))[^，。！？]{0,8}(\d+[万千]?)(?:元)?", combined)
        return {"is_lead": is_lead, "confidence": round(min(.99, .65 + len(set(signals)) * .06), 2), "lead_score": score, "lead_level": level, "intent_level": "high" if score >= 75 else "medium" if score >= 60 else "low", "need": project.get("service", project.get("industry", "")), "location": project.get("location", ""), "budget": budget_match.group(1) if budget_match else "", "time_requirement": "近期" if any(word in combined for word in ("准备", "最近", "年底")) else None, "purchase_stage": "comparison" if any(word in combined for word in ("推荐", "怎么选", "对比")) else "research", "pain_point": "担心增项与踩坑" if any(word in combined for word in ("坑", "增项")) else "", "buying_signals": signals, "summary": f"{comment.get('nickname', '该用户')}正在表达真实的{project.get('industry', '服务')}需求。" if is_lead else "当前评论更像泛互动。", "reason": "出现明确询价、地域、预算或联系方式信号，且已合并用户历史评论上下文。" if is_lead else "缺少明确的购买问题或需求上下文。", "recommended_action": "priority_follow_up" if level == "S" else "follow_up" if is_lead else "observe"}
