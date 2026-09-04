import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings


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
    """Text-only structured completion contract used by every Agent."""

    model = "deterministic-mock"
    configured = False

    def __init__(self):
        self.last_call: LLMCall | None = None

    def clear_last_call(self) -> None:
        self.last_call = None

    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_call = LLMCall(self.model, f"{system}\n\n{user}", {}, 0, 0, True)
        return {}


class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI-compatible text model transport."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__()
        self.settings = settings
        self.model = settings.llm_model
        self.configured = bool(settings.llm_base_url and settings.llm_api_key)
        self.transport = transport

    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        input_text = f"{system}\n\n{user}"
        if not self.configured:
            self.last_call = LLMCall("deterministic-mock", input_text, {}, 0, 0, True)
            return {}

        started = time.perf_counter()
        try:
            body = {
                "model": self.settings.llm_model,
                "temperature": self.settings.llm_temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("文本模型返回内容不是 JSON 字符串")
            output = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
            if not isinstance(output, dict):
                raise ValueError("文本模型返回的 JSON 根节点必须是对象")
            usage = payload.get("usage") or {}
            self.last_call = LLMCall(
                self.settings.llm_model,
                input_text,
                output,
                int(usage.get("total_tokens") or 0),
                round((time.perf_counter() - started) * 1000),
                True,
            )
            return output
        except Exception as exc:
            self.last_call = LLMCall(
                self.settings.llm_model,
                input_text,
                {},
                0,
                round((time.perf_counter() - started) * 1000),
                False,
                str(exc),
            )
            raise

    async def test_connection(self) -> dict[str, Any]:
        if not self.settings.llm_base_url or not self.settings.llm_api_key:
            return {"ok": False, "message": "请先填写 Base URL 和 API Key"}
        try:
            headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout, transport=self.transport) as client:
                response = await client.get(f"{self.settings.llm_base_url.rstrip('/')}/models", headers=headers)
                response.raise_for_status()
            return {"ok": True, "message": f"连接成功：{self.settings.llm_model}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


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
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
