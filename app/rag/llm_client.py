from __future__ import annotations

import json
from typing import Any

import httpx

from app.settings import settings


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LLMClient:
    """OpenAI 兼容的模型客户端；未配置时返回 None，由确定性解析/模板兜底。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url or "").rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)

    async def chat(self, *, system: str, user: str, json_mode: bool = False) -> str:
        if not self.enabled:
            raise LLMError("llm_not_configured", "未配置模型服务")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise LLMError("llm_request_failed", f"模型请求失败: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise LLMError("llm_response_invalid", "模型返回不是合法 JSON") from exc
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("llm_response_invalid", "模型返回缺少 choices.message.content") from exc

    async def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        content = await self.chat(system=system, user=user, json_mode=True)
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("llm_json_invalid", "模型输出不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise LLMError("llm_json_invalid", "模型输出必须是 JSON 对象")
        return value


llm_client = LLMClient()
