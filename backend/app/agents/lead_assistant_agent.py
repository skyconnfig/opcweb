import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


class LeadAssistantDecision(BaseModel):
    """Text-only sales follow-up advice that never sends a reply."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    reason: str
    risk: str
    next_action: str


class LeadAssistantAgent:
    """Generate a human-reviewable follow-up suggestion from text context."""

    prompt_version = "lead_assistant_text_v1"
    _prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "lead_assistant.md"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(
        self,
        project: Mapping[str, Any],
        lead: Mapping[str, Any],
        comments: Sequence[Mapping[str, Any] | str],
        knowledge: Sequence[Mapping[str, Any] | str],
        persona: Mapping[str, Any] | None = None,
    ) -> LeadAssistantDecision:
        if self.llm is None or not self.llm.configured:
            raise LLMNotConfiguredError("LeadAssistantAgent 需要已配置的文本模型")

        payload = {
            "project": self._text_only(project),
            "lead": self._text_only(lead),
            "comment_history": self._text_only(comments),
            "knowledge": self._text_only(knowledge),
            "persona": self._text_only(persona or {}),
        }
        system = self._prompt()
        last_error: LLMInvalidResponseError | None = None
        for attempt in range(2):
            try:
                prompt = system if attempt == 0 else system + " 上一次输出不符合四字段契约，请重新返回完整 JSON。"
                result = await self.llm.structured_output(prompt, json.dumps(payload, ensure_ascii=False), self._schema())
                try:
                    return LeadAssistantDecision.model_validate(result)
                except ValidationError as exc:
                    raise LLMInvalidResponseError(f"LeadAssistantAgent 返回不符合字段契约：{exc}") from exc
            except LLMInvalidResponseError as exc:
                last_error = exc
                if self.llm.last_call is not None:
                    self.llm.last_call.success = False
                    self.llm.last_call.error = str(exc)
        raise last_error or LLMInvalidResponseError("LeadAssistantAgent 输出无效")

    @staticmethod
    def _schema() -> dict[str, Any]:
        return LeadAssistantDecision.model_json_schema()

    @classmethod
    def _prompt(cls) -> str:
        return cls._prompt_path.read_text(encoding="utf-8")

    @classmethod
    def _text_only(cls, value: Any) -> Any:
        media_keys = ("image", "video_frame", "screenshot", "ocr", "embedding", "cover", "thumbnail", "frame", "video_url")
        if isinstance(value, Mapping):
            return {
                str(key): cls._text_only(item)
                for key, item in value.items()
                if not any(token in str(key).lower() for token in media_keys)
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [cls._text_only(item) for item in value]
        return value
