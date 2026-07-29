from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

import httpx

from agentarium.config.settings import Settings

from .base import LLMProvider, ProviderDiagnostic
from .http_providers import OllamaProvider, OpenAICompatibleProvider
from .mock import MockProvider


class ProviderRegistry:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._providers: dict[str, LLMProvider] = {
            "mock": MockProvider(),
            "ollama": OllamaProvider(settings.ollama_url, transport=transport),
            "openai_compatible": OpenAICompatibleProvider(
                settings.openai_compatible_url,
                settings.openai_api_key,
                transport=transport,
            ),
        }
        self._diagnostic_cache: tuple[float, list[ProviderDiagnostic]] | None = None
        self._diagnostic_lock = asyncio.Lock()

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown provider: {name}") from exc

    async def diagnostics(self, *, force: bool = False) -> list[ProviderDiagnostic]:
        if (
            not force
            and self._diagnostic_cache is not None
            and monotonic() - self._diagnostic_cache[0] < 5
        ):
            return [item.model_copy(deep=True) for item in self._diagnostic_cache[1]]

        async with self._diagnostic_lock:
            if (
                not force
                and self._diagnostic_cache is not None
                and monotonic() - self._diagnostic_cache[0] < 5
            ):
                return [item.model_copy(deep=True) for item in self._diagnostic_cache[1]]
            ollama, compatible = await asyncio.gather(
                self._probe_ollama(),
                self._probe_openai_compatible(),
            )
            diagnostics = [
                ProviderDiagnostic(
                    name="mock",
                    label="Motor determinista",
                    reachable=True,
                    ready=True,
                    models=[],
                    message="Respaldo local estable; no realiza inferencia con un modelo.",
                ),
                ollama,
                compatible,
            ]
            self._diagnostic_cache = (monotonic(), diagnostics)
            return [item.model_copy(deep=True) for item in diagnostics]

    async def diagnostic(
        self,
        name: str,
        *,
        force: bool = False,
    ) -> ProviderDiagnostic:
        if name not in self._providers:
            raise ValueError(f"Unknown provider: {name}")
        diagnostics = await self.diagnostics(force=force)
        return next(item for item in diagnostics if item.name == name)

    async def _probe_ollama(self) -> ProviderDiagnostic:
        endpoint = self._settings.ollama_url.rstrip("/")
        try:
            payload = await self._get_json(f"{endpoint}/api/tags")
            raw_models = payload.get("models", [])
            models = sorted(
                {
                    str(item.get("model") or item.get("name")).strip()
                    for item in raw_models
                    if isinstance(item, dict) and (item.get("model") or item.get("name"))
                }
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ProviderDiagnostic(
                name="ollama",
                label="Ollama local",
                endpoint=endpoint,
                reachable=False,
                ready=False,
                message=f"No responde en {endpoint}: {_short_error(exc)}",
            )
        if not models:
            return ProviderDiagnostic(
                name="ollama",
                label="Ollama local",
                endpoint=endpoint,
                reachable=True,
                ready=False,
                message="Servidor disponible, pero todavía no tiene modelos instalados.",
            )
        return ProviderDiagnostic(
            name="ollama",
            label="Ollama local",
            endpoint=endpoint,
            reachable=True,
            ready=True,
            models=models,
            message=_available_message(models),
        )

    async def _probe_openai_compatible(self) -> ProviderDiagnostic:
        endpoint = self._settings.openai_compatible_url.rstrip("/")
        try:
            payload = await self._get_json(
                f"{endpoint}/models",
                authenticated=True,
            )
            raw_models = payload.get("data", [])
            models = sorted(
                {
                    str(item.get("id")).strip()
                    for item in raw_models
                    if isinstance(item, dict) and item.get("id")
                }
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ProviderDiagnostic(
                name="openai_compatible",
                label="Servidor compatible",
                endpoint=endpoint,
                reachable=False,
                ready=False,
                message=f"No responde en {endpoint}: {_short_error(exc)}",
            )
        if not models:
            return ProviderDiagnostic(
                name="openai_compatible",
                label="Servidor compatible",
                endpoint=endpoint,
                reachable=True,
                ready=False,
                message="Servidor disponible, pero no publicó modelos utilizables.",
            )
        return ProviderDiagnostic(
            name="openai_compatible",
            label="Servidor compatible",
            endpoint=endpoint,
            reachable=True,
            ready=True,
            models=models,
            message=_available_message(models),
        )

    async def _get_json(self, url: str, *, authenticated: bool = False) -> dict[str, Any]:
        headers = {}
        if authenticated and self._settings.openai_api_key:
            headers["Authorization"] = f"Bearer {self._settings.openai_api_key}"
        timeout = httpx.Timeout(self._settings.provider_probe_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self._transport,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("La respuesta de diagnóstico no es un objeto JSON")
        return payload


def _short_error(error: Exception) -> str:
    text = str(error).strip().replace("\n", " ")
    if not text:
        return error.__class__.__name__
    return text[:180]


def _available_message(models: list[str]) -> str:
    suffix = "s" if len(models) != 1 else ""
    return f"{len(models)} modelo{suffix} disponible{suffix}."
