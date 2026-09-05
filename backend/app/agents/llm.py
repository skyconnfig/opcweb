import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.errors import LLMError, LLMInvalidResponseError, LLMNotConfiguredError, LLMRequestError


@dataclass
class LLMCall:
    model: str
    input_text: str
    output_json: dict[str, Any]
    tokens: int = 0
    latency_ms: int = 0
    success: bool = True
    error: str = ""


class BaseLLMProvider:
    """Structured completion contract for text-only models."""

    model = ""
    configured = False

    def __init__(self):
        self.last_call: LLMCall | None = None

    def clear_last_call(self) -> None:
        self.last_call = None

    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise LLMError("当前 Provider 未实现结构化输出")

class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI-compatible transport for text completions."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__()
        self.settings = settings
        self.model = settings.llm_model
        self.configured = bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)
        self.transport = transport

    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        input_text = f"{system}\n\n{user}"
        if not self.configured:
            error = LLMNotConfiguredError("请配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
            self.last_call = LLMCall(self.model, input_text, {}, 0, 0, False, str(error))
            raise error

        started = time.perf_counter()
        try:
            body = {
                "model": self.settings.llm_model,
                "temperature": self.settings.llm_temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self._message_content(system)},
                    {"role": "user", "content": self._message_content(user)},
                ],
            }
            # DeepSeek V4 uses the content-block form for text messages.  Its
            # default reasoning mode can consume the completion budget before
            # emitting JSON, so structured application calls explicitly disable
            # reasoning while remaining text-only.
            if self.settings.llm_model.lower().startswith("deepseek-v4"):
                body["thinking"] = {"type": "disabled"}
                body["max_tokens"] = 8000
            try:
                return await self._request_structured(body, self.settings.llm_model, input_text, started)
            except LLMRequestError as exc:
                # DeepSeek V4 is currently served by compatible API nodes that
                # disagree on whether text content is encoded as a string or
                # an OpenAI content block.  Retry only that explicit schema
                # mismatch; auth, quota, timeout and server errors must fail.
                if not self.settings.llm_model.lower().startswith("deepseek-v4") or not self._is_content_shape_error(exc):
                    raise
                fallback = deepcopy(body)
                fallback["messages"] = [
                    {**message, "content": self._toggle_text_content(message["content"])}
                    for message in body["messages"]
                ]
                return await self._request_structured(fallback, self.settings.llm_model, input_text, started)
        except LLMInvalidResponseError:
            raise
        except LLMError:
            raise

    async def _request_structured(
        self, body: dict[str, Any], model: str, input_text: str, started: float
    ) -> dict[str, Any]:
        try:
            headers = {
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=body,
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail = response.text.strip().replace("\n", " ")[:500]
                    suffix = f": {detail}" if detail else ""
                    raise LLMRequestError(f"模型请求失败：HTTP {response.status_code}{suffix}") from exc
            payload = response.json()
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length":
                raise LLMInvalidResponseError("文本模型输出因达到 max_tokens 被截断")
            content = self._response_content(payload)
            output = self._parse_json_object(content)
            usage = payload.get("usage") or {}
            self.last_call = LLMCall(
                model,
                input_text,
                output,
                int(usage.get("total_tokens") or 0),
                round((time.perf_counter() - started) * 1000),
                True,
            )
            return output
        except LLMInvalidResponseError as exc:
            self._record_failure(model, input_text, started, exc)
            raise
        except httpx.HTTPError as exc:
            error = LLMRequestError(f"模型请求失败：{exc}")
            self._record_failure(model, input_text, started, error)
            raise error from exc
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            error = LLMInvalidResponseError(f"模型响应无效：{exc}")
            self._record_failure(model, input_text, started, error)
            raise error from exc
        except Exception as exc:
            error = LLMRequestError(f"模型调用失败：{exc}")
            self._record_failure(model, input_text, started, error)
            raise error from exc

    @staticmethod
    def _response_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise LLMInvalidResponseError("文本模型响应根节点必须是对象")
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError("文本模型响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidResponseError("文本模型返回内容必须是非空 JSON 字符串")
        return content

    def _message_content(self, value: str) -> str | list[dict[str, str]]:
        if self.settings.llm_model.lower().startswith("deepseek-v4"):
            return [{"type": "text", "text": value}]
        return value

    @staticmethod
    def _toggle_text_content(value: str | list[dict[str, str]]) -> str | list[dict[str, str]]:
        if isinstance(value, list):
            return "".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
        return [{"type": "text", "text": value}]

    @staticmethod
    def _is_content_shape_error(error: LLMRequestError) -> bool:
        message = str(error).lower()
        return "messages[0]" in message and "invalid type" in message and ("string" in message or "sequence" in message)

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```").removeprefix("json").strip()
            cleaned = cleaned.removesuffix("```").strip()
        try:
            output = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError(f"文本模型返回内容不是有效 JSON：{exc.msg}") from exc
        if not isinstance(output, dict):
            raise LLMInvalidResponseError("文本模型返回的 JSON 根节点必须是对象")
        return output

    def _record_failure(self, model: str, input_text: str, started: float, error: LLMError) -> None:
        self.last_call = LLMCall(
            model,
            input_text,
            {},
            0,
            round((time.perf_counter() - started) * 1000),
            False,
            str(error),
        )

    async def test_connection(self) -> dict[str, Any]:
        if not self.configured:
            error = LLMNotConfiguredError("请先填写 Base URL、API Key 和 Model")
            return {"ok": False, "code": error.code, "message": error.message}
        try:
            # A models listing only proves that the endpoint is reachable.  The
            # settings screen must verify the same text completion contract used
            # by the agents, including JSON parsing and the configured model.
            output = await self.structured_output(
                system="你是文本模型连接测试助手。只返回 JSON，不要输出 Markdown 或调用工具。",
                user='请仅返回一个 JSON 对象：{"ok": true}。',
                schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            )
            if output.get("ok") is not True:
                error = LLMInvalidResponseError('文本模型连接测试必须返回 {"ok": true}')
                if self.last_call:
                    self.last_call.success = False
                    self.last_call.error = str(error)
                raise error
            return {"ok": True, "message": f"连接成功：{self.settings.llm_model}"}
        except LLMError as exc:
            return {"ok": False, "code": exc.code, "message": exc.message}


def settings_with_db(settings: Settings, values: dict[str, str]) -> Settings:
    """Apply persisted text-model settings without exposing secrets to callers."""

    updates: dict[str, Any] = {}
    for key in ("llm_base_url", "llm_api_key", "llm_model", "llm_temperature", "llm_timeout"):
        if key not in values or values[key] == "":
            continue
        value: Any = values[key]
        if key in {"llm_temperature", "llm_timeout"}:
            value = float(value)
        updates[key] = value
    return settings.model_copy(update=updates)


def input_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
