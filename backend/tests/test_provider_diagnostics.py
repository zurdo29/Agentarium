from __future__ import annotations

import httpx
import pytest
from agentarium.config.settings import Settings
from agentarium.llm import ProviderRegistry


@pytest.mark.asyncio
async def test_registry_discovers_local_models_without_exposing_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:4b", "model": "qwen3:4b"},
                        {"name": "gemma3:4b", "model": "gemma3:4b"},
                    ]
                },
            )
        if request.url.path == "/v1/models":
            assert request.headers["Authorization"] == "Bearer local-secret"
            return httpx.Response(
                200,
                json={"data": [{"id": "local-coder"}, {"id": "local-general"}]},
            )
        return httpx.Response(404)

    registry = ProviderRegistry(
        Settings(
            ollama_url="http://ollama.test",
            openai_compatible_url="http://compatible.test/v1",
            openai_api_key="local-secret",
        ),
        transport=httpx.MockTransport(handler),
    )

    diagnostics = {item.name: item for item in await registry.diagnostics()}

    assert diagnostics["mock"].ready is True
    assert diagnostics["ollama"].models == ["gemma3:4b", "qwen3:4b"]
    assert diagnostics["openai_compatible"].models == [
        "local-coder",
        "local-general",
    ]
    assert "local-secret" not in diagnostics["openai_compatible"].model_dump_json()


@pytest.mark.asyncio
async def test_registry_distinguishes_reachable_server_without_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        raise httpx.ConnectError("connection refused", request=request)

    registry = ProviderRegistry(
        Settings(
            ollama_url="http://ollama.test",
            openai_compatible_url="http://compatible.test/v1",
        ),
        transport=httpx.MockTransport(handler),
    )

    diagnostics = {item.name: item for item in await registry.diagnostics()}

    assert diagnostics["ollama"].reachable is True
    assert diagnostics["ollama"].ready is False
    assert "no tiene modelos" in diagnostics["ollama"].message
    assert diagnostics["openai_compatible"].reachable is False
    assert diagnostics["openai_compatible"].ready is False
