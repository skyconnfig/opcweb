import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


BUYING_TERMS = ["多少钱", "预算", "推荐", "靠谱", "联系", "怎么选", "准备", "需要", "能做吗", "报价", "增项", "翻新", "装修", "买", "哪里有"]
NOISE_TERMS = ["哈哈", "666", "好看", "主播好帅", "路过", "支持", "收藏了", "讲得很好", "学到了"]


class LeadJudgement(BaseModel):
    """Strict text-only contract for the LeadJudge output."""

    model_config = ConfigDict(extra="ignore")

    is_lead: bool
    confidence: float = Field(ge=0, le=1)
    lead_score: float = Field(ge=0, le=100)
    lead_level: Literal["S", "A", "B", "C"] | None = None
    intent: str = ""
    intent_level: str
    need: str
    location: str | None = None
    budget: str | None = None
    time_requirement: str | None = None
    purchase_stage: str
    pain_point: str
    buying_signals: list[str]
    summary: str
    reason: str
    recommended_action: str
    should_reply: bool


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
        if self.llm is None:
            raise LLMNotConfiguredError("LeadJudgeAgent 需要已配置的文本模型")
        prompt = "你是潜客判断 Agent。只分析文字评论、评论上下文、来源视频文字字段和公开互动数据，不使用任何画面。严格返回 JSON，并保留 is_lead、confidence、lead_score、lead_level、intent_level、need、location、budget、time_requirement、purchase_stage、pain_point、buying_signals、summary、reason、recommended_action、should_reply。"
        payload = json.dumps({"project": self._text_context(project), "comment": comment}, ensure_ascii=False)
        last_error = None
        for _ in range(2):
            try:
                result = await self.llm.structured_output(prompt, payload, {"type": "object"})
                return self._normalize(result, project, comment)
            except Exception as exc:
                last_error = exc
        raise last_error

    def _normalize(self, result: dict, project: dict, comment: dict) -> dict:
        required = ("is_lead", "confidence", "lead_score", "intent_level", "need", "location", "budget", "time_requirement", "purchase_stage", "pain_point", "buying_signals", "summary", "reason", "recommended_action", "should_reply")
        missing = [key for key in required if key not in result]
        if missing:
            raise LLMInvalidResponseError(f"LeadJudgeAgent 缺少字段: {', '.join(missing)}")
        normalized = dict(result)
        for key in ("is_lead", "should_reply"):
            normalized[key] = _parse_bool(normalized[key], key)
        normalized["intent_level"] = _normalize_intent_level(normalized.get("intent_level"))
        for key in ("need", "purchase_stage", "pain_point", "summary", "reason", "recommended_action"):
            normalized[key] = _text_value(normalized.get(key))
        for key in ("location", "budget", "time_requirement"):
            if normalized.get(key) is not None:
                normalized[key] = _text_value(normalized[key])
        normalized["buying_signals"] = _normalize_signals(normalized.get("buying_signals"))
        if normalized.get("intent") is not None:
            normalized["intent"] = _text_value(normalized["intent"])
        # The score is the source of truth for the persisted lead level.  Text
        # models sometimes return localized labels such as "低" or "高" even
        # when the JSON contract asks for S/A/B/C; normalize those labels
        # before validation so one localized enum cannot abort a scan task.
        normalized["lead_level"] = _normalize_lead_level(normalized.get("lead_level"))
        try:
            validated = LeadJudgement.model_validate(normalized)
        except (ValidationError, TypeError, ValueError) as exc:
            raise LLMInvalidResponseError(f"LeadJudgeAgent 输出校验失败: {exc}") from exc
        output = validated.model_dump()
        output["lead_score"] = max(0, min(100, float(output["lead_score"])))
        output["lead_level"] = "S" if output["lead_score"] >= 90 else "A" if output["lead_score"] >= 75 else "B" if output["lead_score"] >= 60 else "C"
        output["intent"] = output.get("intent") or output.get("intent_level", "")
        return output

    @staticmethod
    def _text_context(project: dict) -> dict:
        return {key: str(project.get(key, "")) for key in ("industry", "location", "service", "target_customer", "price_range", "description", "keyword", "video_title", "video_description", "video_creator", "video_likes", "video_comments", "video_shares", "video_collects", "history_text")}


def _parse_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "是", "有"}:
            return True
        if normalized in {"false", "0", "no", "否", "无"}:
            return False
    raise LLMInvalidResponseError(f"LeadJudgeAgent 字段 {field} 必须是布尔值")


def _text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _normalize_intent_level(value: object) -> str:
    if isinstance(value, bool):
        return "high" if value else "low"
    if isinstance(value, (int, float)):
        return {0: "low", 1: "medium", 2: "high"}.get(int(value), str(value))
    return _text_value(value).lower()


def _normalize_signals(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、;；\n]+", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [_text_value(item) for item in value if _text_value(item)]
    return []


def _normalize_lead_level(value: object) -> str | None:
    if value is None or value == "":
        return None
    normalized = str(value).strip().upper()
    aliases = {
        "S级": "S",
        "A级": "A",
        "B级": "B",
        "C级": "C",
        "高": "S",
        "较高": "A",
        "中": "B",
        "中等": "B",
        "低": "C",
        "HIGH": "S",
        "MEDIUM": "B",
        "LOW": "C",
    }
    return aliases.get(normalized, normalized if normalized in {"S", "A", "B", "C"} else None)
