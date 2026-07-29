from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentarium.domain.models import AgentDefinition


class ModelRequest(BaseModel):
    operation: str
    project_id: str
    work_item_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    payload: dict[str, Any]


class ProviderResponse(BaseModel):
    content: dict[str, Any]
    raw_text: str
    prompt_characters: int = Field(ge=0)
    response_characters: int = Field(ge=0)


class ProviderDiagnostic(BaseModel):
    name: Literal["mock", "ollama", "openai_compatible"]
    label: str
    endpoint: str | None = None
    reachable: bool
    ready: bool
    models: list[str] = Field(default_factory=list)
    message: str


class ProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def generate(
        self,
        request: ModelRequest,
        agent: AgentDefinition,
    ) -> ProviderResponse:
        raise NotImplementedError
