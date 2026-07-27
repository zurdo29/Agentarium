from __future__ import annotations

from agentarium.config.settings import Settings

from .base import LLMProvider
from .http_providers import OllamaProvider, OpenAICompatibleProvider
from .mock import MockProvider


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._providers: dict[str, LLMProvider] = {
            "mock": MockProvider(),
            "ollama": OllamaProvider(settings.ollama_url),
            "openai_compatible": OpenAICompatibleProvider(
                settings.openai_compatible_url,
                settings.openai_api_key,
            ),
        }

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown provider: {name}") from exc
