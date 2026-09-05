import json

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


CATEGORIES = ["核心词", "需求词", "购买意向", "痛点词", "问题词", "价格词", "对比词", "避坑词", "竞品词", "地域词", "场景词", "人群词", "长尾词"]


class KeywordAgent:
    prompt_version = "keyword_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, context: dict, intelligence: dict) -> list[dict]:
        if self.llm is None:
            raise LLMNotConfiguredError("KeywordAgent 需要已配置的文本模型")
        system = """你是关键词 Agent，只能根据行业文字、客户语言、评论文字和结构化字段生成关键词，绝不读取或推断任何图片、视频画面或 OCR。
只输出一个 JSON 对象，格式必须是 {"keywords":[{"keyword":"词","category":"核心词"}]}。
keywords 必须恰好包含 100 个去重对象（100 个即可，不要生成 200 或 300 个）；每项只保留 keyword 和 category，category 只能使用：核心词、需求词、购买意向、痛点词、问题词、价格词、对比词、避坑词、竞品词、地域词、场景词、人群词、长尾词。不要输出 analysis、recommendations 或其他顶层字段。""",
        payload = json.dumps({"project": self._text_context(context), "intelligence": intelligence}, ensure_ascii=False)
        last_error = None
        for attempt in range(3):
            try:
                request_payload = payload
                result = await self.llm.structured_output(
                    system if attempt == 0 else f"{system}\n这是第 {attempt + 1} 次尝试。上一次输出数量或字段不合格，请从头生成恰好 100 个去重关键词对象，并只返回 keywords。",
                    request_payload,
                    {"type": "object"},
                )
                rows = result.get("keywords", []) if isinstance(result, dict) else []
                normalized = self._normalize(rows, context)
                if not normalized:
                    raise LLMInvalidResponseError("KeywordAgent 必须返回 100 到 300 个有效关键词")
                return normalized
            except LLMInvalidResponseError as exc:
                last_error = exc
                if self.llm.last_call is not None:
                    self.llm.last_call.success = False
                    self.llm.last_call.error = str(exc)
        raise last_error

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
