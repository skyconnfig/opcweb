import hashlib
import json
from typing import Any

import httpx

from app.core.config import Settings


class BaseLLMProvider:
    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {}


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def structured_output(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.llm_base_url or not self.settings.llm_api_key:
            return await MockLLMProvider().structured_output(system, user, schema)
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        body = {"model": self.settings.llm_model, "temperature": 0.2, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{self.settings.llm_base_url.rstrip('/')}/chat/completions", headers=headers, json=body)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)


def input_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

