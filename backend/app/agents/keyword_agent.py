import json

from app.agents.llm import BaseLLMProvider


CATEGORIES = ["核心词", "需求词", "购买意向", "痛点词", "问题词", "价格词", "对比词", "避坑词", "竞品词", "地域词", "场景词", "人群词", "长尾词"]


class KeywordAgent:
    prompt_version = "keyword_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, context: dict, intelligence: dict) -> list[dict]:
        if self.llm and self.llm.configured:
            result = await self.llm.structured_output(
                "你是关键词 Agent。只根据行业文字、客户语言和结构化字段生成 100 到 300 个关键词，不读取或推断任何画面。返回 JSON：{keywords:[...]}。",
                json.dumps({"project": self._text_context(context), "intelligence": intelligence}, ensure_ascii=False),
                {"type": "object"},
            )
            rows = result.get("keywords", []) if isinstance(result, dict) else []
            normalized = self._normalize(rows, context)
            if normalized:
                return normalized
        return self.generate(context, intelligence)

    def generate(self, context: dict, intelligence: dict) -> list[dict]:
        industry = context.get("industry", "服务")
        location = context.get("location", "本地")
        service = context.get("service", "专业服务")
        target = context.get("target_customer", "有明确需求的客户")
        bases = [industry, f"{location}{industry}", f"{location}{industry}公司", f"{location}{industry}推荐", f"{location}{industry}哪家好", f"{location}{industry}多少钱", f"{service}怎么选", f"{industry}预算", f"{industry}避坑", f"{industry}报价", f"{industry}踩坑", f"有没有靠谱的{industry}", f"准备做{service}", f"{location}本地{service}", target]
        suffixes = ["多少钱", "哪家靠谱", "怎么收费", "有推荐吗", "怎么选", "预算多少", "能做吗", "会不会有增项", "需要注意什么", "有没有联系方式", "适合什么人", "价格对比"]
        rows = []
        seen: set[str] = set()
        for index in range(120):
            base = bases[index % len(bases)]
            keyword = base if index < len(bases) else f"{base}{suffixes[index % len(suffixes)]}"
            if keyword in seen:
                keyword = f"{keyword} {index + 1}"
            seen.add(keyword)
            category = CATEGORIES[index % len(CATEGORIES)]
            intent = min(98, 58 + (index * 7) % 40)
            commercial = min(98, 55 + (index * 11) % 43)
            opportunity = keyword_opportunity_score(intent, commercial, 74, 90, 82, 0)
            rows.append({"keyword": keyword, "category": category, "intent_score": intent, "commercial_score": commercial, "opportunity_score": opportunity, "enabled": True, "source": "text-ai" if self.llm and self.llm.configured else "deterministic-mock", "reason": f"含有{location}与{industry}语境，适合发现明确需求。"})
        return rows

    def _normalize(self, rows: list, context: dict) -> list[dict]:
        normalized = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if isinstance(row, str):
                row = {"keyword": row}
            if not isinstance(row, dict) or not str(row.get("keyword", "")).strip():
                continue
            keyword = str(row["keyword"]).strip()
            if keyword in seen:
                continue
            seen.add(keyword)
            intent = float(row.get("intent_score", 70))
            commercial = float(row.get("commercial_score", 70))
            normalized.append({"keyword": keyword, "category": str(row.get("category", CATEGORIES[index % len(CATEGORIES)])), "intent_score": intent, "commercial_score": commercial, "opportunity_score": float(row.get("opportunity_score", keyword_opportunity_score(intent, commercial))), "enabled": bool(row.get("enabled", True)), "source": "text-ai", "reason": str(row.get("reason", "文本模型基于客户语言推荐"))})
        return normalized[:300] if len(normalized) >= 100 else []

    @staticmethod
    def _text_context(context: dict) -> dict:
        return {key: str(context.get(key, "")) for key in ("industry", "location", "service", "target_customer", "price_range", "description")}


def keyword_opportunity_score(intent, commercial, clarity=74, local=90, ai=82, history=0):
    return round(intent * .30 + commercial * .20 + clarity * .20 + local * .10 + ai * .10 + history * .10, 2)
