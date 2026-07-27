from __future__ import annotations

import json
from typing import Any

import httpx

from agentarium.domain.models import AgentDefinition

from .base import LLMProvider, ModelRequest, ProviderError, ProviderResponse


def _prompt(request: ModelRequest) -> str:
    return (
        "Return exactly one JSON object. Do not include markdown. "
        "Your response must be a terminal structured artifact, a concrete blocker, "
        "or an approval request.\n"
        + json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
    )


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Provider returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("Provider JSON must be an object")
    return parsed


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def generate(
        self,
        request: ModelRequest,
        agent: AgentDefinition,
    ) -> ProviderResponse:
        prompt = _prompt(request)
        payload = {
            "model": agent.model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": agent.temperature,
                "num_ctx": agent.context_limit,
                "num_predict": agent.output_token_limit,
            },
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with httpx.AsyncClient(timeout=agent.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        data = response.json()
        raw = str(data.get("message", {}).get("content", ""))
        return ProviderResponse(
            content=_parse_json(raw),
            raw_text=raw,
            prompt_characters=len(prompt),
            response_characters=len(raw),
        )


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def generate(
        self,
        request: ModelRequest,
        agent: AgentDefinition,
    ) -> ProviderResponse:
        prompt = _prompt(request)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {
            "model": agent.model,
            "temperature": agent.temperature,
            "max_tokens": agent.output_token_limit,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with httpx.AsyncClient(timeout=agent.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI-compatible request failed: {exc}") from exc
        data = response.json()
        raw = str(data["choices"][0]["message"]["content"])
        return ProviderResponse(
            content=_parse_json(raw),
            raw_text=raw,
            prompt_characters=len(prompt),
            response_characters=len(raw),
        )
