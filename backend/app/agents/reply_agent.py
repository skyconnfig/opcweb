import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.llm import BaseLLMProvider
from app.errors import LLMInvalidResponseError, LLMNotConfiguredError


ReplyType = Literal[
    "normal",
    "question",
    "price",
    "purchase_intent",
    "objection",
    "complaint",
    "after_sales",
    "spam",
]


class ReplyDecision(BaseModel):
    """Validated, text-only decision produced by ReplyAgent."""

    model_config = ConfigDict(extra="forbid")

    should_reply: bool
    confidence: float = Field(ge=0, le=1)
    reply_type: ReplyType
    reply_text: str = ""
    need_human_review: bool
    reason: str
    risk_flags: list[str] = Field(default_factory=list)


class ReplyAgent:
    """Generate a safe reply from text context and matched knowledge only.

    This agent intentionally has no fallback response. A configured text LLM is
    required for eligible comments; blocked cases return a deterministic review
    decision before making a model call.
    """

    prompt_version = "reply_text_v1"
    _prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "reply.md"

    def __init__(self, llm: BaseLLMProvider | None = None):
        self.llm = llm

    async def run(
        self,
        project: Mapping[str, Any],
        comment: Mapping[str, Any],
        lead: Mapping[str, Any] | None = None,
        persona: Mapping[str, Any] | None = None,
        knowledge: Sequence[Mapping[str, Any]] | None = None,
        reply_history: Sequence[Mapping[str, Any] | str] | None = None,
    ) -> ReplyDecision:
        lead = lead or {}
        persona = persona or {}
        reply_history = reply_history or []
        matched_knowledge = self._match_knowledge(project, comment, lead, knowledge or [])

        risk_comment = dict(comment)
        risk_comment["reply_history"] = [
            self._text_only(item) if isinstance(item, Mapping) else str(item) for item in reply_history
        ]
        blocked_flags = self._risk_flags(project, risk_comment, lead)
        if not matched_knowledge:
            return self._blocked(
                "知识库没有足够的、与当前问题匹配的事实，必须由人工确认后处理。",
                ["KNOWLEDGE_INSUFFICIENT", *blocked_flags],
            )
        if blocked_flags:
            return self._blocked("评论或业务上下文包含敏感风险，禁止自动生成或发送回复。", blocked_flags)
        if self.llm is None or not self.llm.configured:
            raise LLMNotConfiguredError("ReplyAgent 需要已配置的文本模型")

        payload = {
            "project": self._text_only(project),
            "comment": self._text_only(comment),
            "lead": self._text_only(lead),
            "persona": self._text_only(persona),
            "knowledge": [self._text_only(entry) for entry in matched_knowledge],
            "reply_history": [self._text_only(item) if isinstance(item, Mapping) else str(item) for item in reply_history],
        }
        input_text = json.dumps(payload, ensure_ascii=False)
        last_error: LLMInvalidResponseError | None = None
        for attempt in range(2):
            try:
                system = self._prompt()
                if attempt:
                    system += "\n上一次回复不符合 JSON 字段契约。请重新返回完整且严格符合 ReplyDecision 的 JSON。"
                result = await self.llm.structured_output(system, input_text, self._schema())
                try:
                    decision = ReplyDecision.model_validate(result)
                except ValidationError as exc:
                    raise LLMInvalidResponseError(f"ReplyAgent 返回不符合 ReplyDecision：{exc}") from exc

                output_flags = self._risk_flags(project, comment, lead, decision.reply_text, decision.risk_flags)
                if output_flags or not matched_knowledge:
                    return self._blocked(
                        "检测到回复内容或上下文存在敏感风险，必须由人工审核。",
                        sorted(set(output_flags or ["KNOWLEDGE_INSUFFICIENT"])),
                    )
                if not decision.reply_text.strip():
                    return self._blocked("模型没有提供可供审核的回复文本。", ["EMPTY_REPLY"])
                return decision
            except LLMInvalidResponseError as exc:
                last_error = exc
                if self.llm.last_call is not None:
                    self.llm.last_call.success = False
                    self.llm.last_call.error = str(exc)
        raise last_error or LLMInvalidResponseError("ReplyAgent 输出无效")

    @classmethod
    def _prompt(cls) -> str:
        return cls._prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def _schema() -> dict[str, Any]:
        return ReplyDecision.model_json_schema()

    @classmethod
    def _blocked(cls, reason: str, risk_flags: list[str]) -> ReplyDecision:
        return ReplyDecision(
            should_reply=False,
            confidence=1,
            reply_type="normal",
            reply_text="",
            need_human_review=True,
            reason=reason,
            risk_flags=list(dict.fromkeys(risk_flags)),
        )

    @classmethod
    def _match_knowledge(
        cls,
        project: Mapping[str, Any],
        comment: Mapping[str, Any],
        lead: Mapping[str, Any],
        entries: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        query = " ".join(
            str(value)
            for value in (
                comment.get("content", ""),
                lead.get("need", ""),
                lead.get("pain_point", ""),
                project.get("industry", ""),
                project.get("service", ""),
                project.get("location", ""),
            )
            if value
        ).lower()
        terms = cls._terms(query)
        matched: list[Mapping[str, Any]] = []
        for entry in entries:
            if not entry.get("enabled", True) or not str(entry.get("content", "")).strip():
                continue
            tags = entry.get("tags", [])
            if isinstance(tags, str):
                tags = re.split(r"[,，、\s]+", tags)
            searchable = " ".join(
                [str(entry.get("title", "")), str(entry.get("content", "")), *[str(tag) for tag in tags]]
            ).lower()
            if any(term in searchable for term in terms):
                matched.append(entry)
        return matched

    @staticmethod
    def _terms(text: str) -> list[str]:
        lowered = text.lower()
        terms: list[str] = []
        for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9][a-z0-9_-]*", lowered):
            if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                # Lightweight, dependency-free Chinese matching: retain
                # useful 2/3-character n-grams instead of treating a whole
                # sentence such as “120平大概多少钱” as one token.
                terms.extend(chunk[index:index + size] for size in (2, 3) for index in range(len(chunk) - size + 1))
                if len(chunk) <= 6:
                    terms.append(chunk)
            else:
                terms.append(chunk)
        aliases = {
            "多少钱": ("价格", "报价", "费用", "收费"),
            "多少": ("价格", "报价", "费用", "收费"),
            "面积": ("平", "平方", "户型"),
            "报价": ("价格", "费用", "收费"),
            "预算": ("价格", "费用", "报价"),
        }
        for source, replacements in aliases.items():
            if source in lowered:
                terms.extend(replacements)
        return list(dict.fromkeys(term for term in terms if len(term) >= 2 and term not in {"客户", "项目", "行业"}))

    @staticmethod
    def _text_only(value: Any) -> Any:
        media_keys = ("image", "video_frame", "screenshot", "ocr", "embedding", "cover", "thumbnail", "video_url", "frame")
        if isinstance(value, Mapping):
            return {
                str(key): ReplyAgent._text_only(item)
                for key, item in value.items()
                if not any(token in str(key).lower() for token in media_keys)
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [ReplyAgent._text_only(item) for item in value]
        return value

    @classmethod
    def _risk_flags(
        cls,
        project: Mapping[str, Any],
        comment: Mapping[str, Any],
        lead: Mapping[str, Any],
        reply_text: str = "",
        model_flags: Sequence[str] | None = None,
    ) -> list[str]:
        text = " ".join(str(value) for value in (*project.values(), *comment.values(), *lead.values(), reply_text)).lower()
        rules = {
            "COMPLAINT_OR_DISPUTE": ("投诉", "退款", "退钱", "纠纷", "售后", "维权"),
            "LEGAL_OR_THREAT": ("律师", "起诉", "法院", "违法", "举报", "威胁"),
            "ABUSE_ESCALATION": ("去死", "骗子", "垃圾", "傻逼", "辱骂"),
            "PLATFORM_OR_ACCOUNT_RISK": ("封号", "处罚", "账号安全", "验证码", "解封"),
            "SENSITIVE_DATA_REQUEST": ("身份证", "银行卡", "密码", "验证码", "身份证号"),
            "UNVERIFIED_PROMISE_OR_PRICE": ("保证", "绝对", "最低价", "全网最低", "免费送", "百分百"),
        }
        flags = [name for name, terms in rules.items() if any(term in text for term in terms)]
        return list(dict.fromkeys([*flags, *(str(flag) for flag in (model_flags or []) if flag)]))
