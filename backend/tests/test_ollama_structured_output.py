from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agentarium.domain.enums import AgentRole
from agentarium.domain.models import AgentDefinition
from agentarium.llm import ModelRequest
from agentarium.llm.base import ProviderError
from agentarium.llm.http_providers import OllamaProvider
from agentarium.llm.prompts import ollama_response_schema


def _agent() -> AgentDefinition:
    return AgentDefinition(
        role=AgentRole.TESTER,
        provider="ollama",
        model="fixture-model",
        temperature=0,
        context_limit=4096,
        output_token_limit=800,
        timeout_seconds=10,
        max_retries=0,
        tools=[],
        allowed_directories=[],
        authority="test_execution",
        execution_budget=1,
    )


@pytest.mark.asyncio
async def test_ollama_receives_the_pydantic_schema_in_format() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "passed": True,
                            "checks": [
                                {
                                    "name": "syntax",
                                    "passed": True,
                                    "evidence": "Validado.",
                                }
                            ],
                            "summary": "Correcto.",
                        }
                    )
                }
            },
        )

    provider = OllamaProvider(
        "http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    request = ModelRequest(
        operation="test",
        project_id="fixture-project",
        work_item_id="fixture-task",
        payload={"acceptance_criteria": ["Funciona"]},
    )

    response = await provider.generate(request, _agent())

    assert response.content["passed"] is True
    assert captured["think"] is False
    assert isinstance(captured["format"], dict)
    assert set(captured["format"]["required"]) == {"passed", "checks", "summary"}
    assert "$defs" not in json.dumps(captured["format"])
    assert "$ref" not in json.dumps(captured["format"])


def test_nested_contract_references_are_inlined_for_ollama_grammar() -> None:
    schema = ollama_response_schema("work")

    assert schema is not None
    serialized = json.dumps(schema)
    assert "$defs" not in serialized
    assert "$ref" not in serialized
    assert "maxLength" not in serialized
    assert "minItems" not in serialized
    file_schema = schema["properties"]["files"]["items"]
    assert set(file_schema["required"]) == {"path", "content", "purpose"}


@pytest.mark.asyncio
async def test_ollama_reports_truncated_structured_output_explicitly() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "done_reason": "length",
                "message": {"content": '{"passed": true, "checks": ['},
            },
        )

    provider = OllamaProvider(
        "http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    request = ModelRequest(
        operation="test",
        project_id="fixture-project",
        work_item_id="fixture-task",
        payload={"acceptance_criteria": ["Funciona"]},
    )

    with pytest.raises(ProviderError, match="context or output limit"):
        await provider.generate(request, _agent())
