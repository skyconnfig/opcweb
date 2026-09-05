import json

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


class PersonaAgent:
    prompt_version = "persona_text_v1"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(self, project: dict, lead: dict, persona: dict, comments: list[str] | None = None) -> dict:
        comments = comments or []
        if self.llm is None:
            raise LLMNotConfiguredError("PersonaAgent 需要已配置的文本模型")
        prompt = (
            "你是人设跟进 Agent。只使用项目文字、人设文字、潜客字段和公开评论文本。"
            "只输出供人工确认的建议，不自动发送，不索取联系方式。"
            "严格返回一个 JSON 对象，并且必须使用以下 5 个字段名："
            "customer_insight（客户洞察字符串）、communication_strategy（沟通策略字符串）、"
            "recommended_reply（推荐回复字符串）、follow_up_question（下一步问题字符串）、"
            "warnings（风险提示字符串数组）。不要输出其它字段，不要使用 markdown。"
        )
        payload = json.dumps(
            {"project": project, "lead": lead, "persona": persona, "comment_history": comments},
            ensure_ascii=False,
        )
        schema = self._schema()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                result = await self.llm.structured_output(
                    prompt if attempt == 0 else prompt + " 上一次字段不符合要求，请按上述字段名重新输出完整 JSON。",
                    payload,
                    schema,
                )
                return self._normalize(result, project, lead)
            except LLMInvalidResponseError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    @staticmethod
    def _normalize(result: dict, project: dict, lead: dict) -> dict:
        required = ("customer_insight", "communication_strategy", "recommended_reply", "follow_up_question", "warnings")
        aliases = {
            "customer_insight": ("customer_insight", "customerInsight", "insight", "customer_profile"),
            "communication_strategy": ("communication_strategy", "communicationStrategy", "strategy"),
            "recommended_reply": ("recommended_reply", "recommendedReply", "reply", "suggested_reply", "response"),
            "follow_up_question": ("follow_up_question", "followUpQuestion", "next_question", "question"),
            "warnings": ("warnings", "risk_warnings", "risks", "warning"),
        }
        normalized = {}
        for key in required:
            for alias in aliases[key]:
                if alias in result:
                    normalized[key] = _text_list(result[alias]) if key == "warnings" else _text(result[alias])
                    break
        missing = [key for key in required if key not in normalized]
        if missing:
            raise LLMInvalidResponseError(f"PersonaAgent 缺少字段: {', '.join(missing)}")
        if not normalized["recommended_reply"].strip():
            raise LLMInvalidResponseError("PersonaAgent recommended_reply 不能为空")
        return normalized

    @staticmethod
    def _schema() -> dict:
        return {
            "type": "object",
            "required": ["customer_insight", "communication_strategy", "recommended_reply", "follow_up_question", "warnings"],
            "properties": {
                "customer_insight": {"type": "string"},
                "communication_strategy": {"type": "string"},
                "recommended_reply": {"type": "string"},
                "follow_up_question": {"type": "string"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace("；", "\n").replace(";", "\n").splitlines() if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []
