from app.agents.llm import input_hash


CATEGORIES = ["核心词", "需求词", "购买意向", "痛点词", "问题词", "价格词", "对比词", "避坑词", "竞品词", "地域词", "场景词", "人群词", "长尾词"]


class KeywordAgent:
    prompt_version = "keyword_v1"

    async def run(self, context: dict, intelligence: dict) -> list[dict]:
        return self.generate(context, intelligence)

    def generate(self, context: dict, intelligence: dict) -> list[dict]:
        industry, location, service = context.get("industry", "服务"), context.get("location", "本地"), context.get("service", "专业服务")
        bases = [industry, f"{location}{industry}", f"{location}{industry}公司", f"{location}{industry}推荐", f"{location}{industry}哪家好", f"{location}{industry}多少钱", f"{service}怎么选", f"{industry}预算", f"{industry}避坑", f"{industry}报价", f"{industry}踩坑", f"有没有靠谱的{industry}", f"准备做{service}", f"{location}本地{service}"]
        suffixes = ["多少钱", "哪家靠谱", "怎么收费", "有推荐吗", "怎么选", "预算多少", "能做吗", "会不会有增项", "需要注意什么", "有没有联系方式"]
        rows = []
        for index in range(50):
            base = bases[index % len(bases)]
            keyword = base if index < len(bases) else f"{base}{suffixes[index % len(suffixes)]}"
            category = CATEGORIES[index % len(CATEGORIES)]
            intent = min(98, 58 + (index * 7) % 40)
            commercial = min(98, 55 + (index * 11) % 43)
            opportunity = round(intent * 0.3 + commercial * 0.2 + 74 * 0.2 + 90 * 0.1 + 82 * 0.1 + 0, 1)
            rows.append({"keyword": keyword, "category": category, "intent_score": intent, "commercial_score": commercial, "opportunity_score": opportunity, "enabled": True, "source": "ai", "reason": f"含有{location}与{industry}语境，适合发现明确需求。"})
        return rows


def keyword_opportunity_score(intent, commercial, clarity=74, local=90, ai=82, history=0):
    return round(intent * .30 + commercial * .20 + clarity * .20 + local * .10 + ai * .10 + history * .10, 2)
