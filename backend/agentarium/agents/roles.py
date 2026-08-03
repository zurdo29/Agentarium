from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import yaml

from agentarium.domain.enums import AgentRole, RunOutcome
from agentarium.domain.models import AgentDefinition, AgentRun, ResourceUsage, new_id
from agentarium.execution.scheduler import AttemptTiming, ResourceScheduler
from agentarium.llm import ModelRequest, ProviderRegistry, ProviderResponse


class RoleCatalog:
    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._defaults: dict[AgentRole, AgentDefinition] = {}
        for role_name, config in raw["roles"].items():
            role = AgentRole(role_name)
            self._defaults[role] = AgentDefinition(role=role, **config)
        self._roles = {
            role: definition.model_copy(deep=True)
            for role, definition in self._defaults.items()
        }

    def select_provider(self, provider: str, model: str | None = None) -> None:
        if provider == "mock":
            self._roles = {
                role: definition.model_copy(
                    update={"provider": "mock"},
                    deep=True,
                )
                for role, definition in self._defaults.items()
            }
            return
        cleaned_model = (model or "").strip()
        if not cleaned_model:
            raise ValueError(f"Provider {provider} requires an explicit model")
        self._roles = {
            role: definition.model_copy(
                update={"provider": provider, "model": cleaned_model},
                deep=True,
            )
            for role, definition in self._defaults.items()
        }

    def active_provider(self) -> str:
        providers = {definition.provider for definition in self._roles.values()}
        return next(iter(providers)) if len(providers) == 1 else "mixed"

    def active_model(self) -> str | None:
        models = {definition.model for definition in self._roles.values()}
        return next(iter(models)) if len(models) == 1 else None

    def get(self, role: AgentRole) -> AgentDefinition:
        return self._roles[role]

    def all(self) -> list[AgentDefinition]:
        return list(self._roles.values())


class RoleRunner:
    def __init__(
        self,
        catalog: RoleCatalog,
        providers: ProviderRegistry,
        scheduler: ResourceScheduler,
    ) -> None:
        self.catalog = catalog
        self.providers = providers
        self.scheduler = scheduler

    async def run(
        self,
        role: AgentRole,
        request: ModelRequest,
        *,
        correlation_id: str,
    ) -> tuple[ProviderResponse, AgentRun]:
        definition = self.catalog.get(role)
        provider = self.providers.get(definition.provider)
        started = datetime.now(UTC)
        clock = perf_counter()
        last_error: Exception | None = None
        errors = 0
        total_queue_wait_ms = 0
        total_generation_ms = 0
        generation_started = False

        def accumulate(timing: AttemptTiming) -> None:
            nonlocal total_queue_wait_ms, total_generation_ms, generation_started
            if timing.queue_wait_ms is not None:
                total_queue_wait_ms += timing.queue_wait_ms
            if timing.generation_ms is not None:
                generation_started = True
                total_generation_ms += timing.generation_ms

        for retry in range(definition.max_retries + 1):
            timing = AttemptTiming()
            try:
                response = await asyncio.wait_for(
                    self.scheduler.run(
                        lambda: provider.generate(request, definition), timing
                    ),
                    timeout=definition.timeout_seconds,
                )
            except Exception as exc:
                errors += 1
                last_error = exc
                accumulate(timing)
                if retry < definition.max_retries:
                    await asyncio.sleep(min(0.1 * (2**retry), 1))
                continue

            accumulate(timing)
            duration_ms = int((perf_counter() - clock) * 1000)
            usage = ResourceUsage(
                duration_ms=duration_ms,
                queue_wait_ms=total_queue_wait_ms,
                generation_ms=total_generation_ms if generation_started else None,
                prompt_characters=response.prompt_characters,
                response_characters=response.response_characters,
                prompt_tokens_approx=response.prompt_characters // 4,
                response_tokens_approx=response.response_characters // 4,
                model=definition.model,
                provider=definition.provider,
                errors=errors,
            )
            run = AgentRun(
                project_id=request.project_id,
                work_item_id=request.work_item_id,
                agent_role=role,
                model=definition.model,
                provider=definition.provider,
                attempt=request.attempt,
                outcome=RunOutcome.ARTIFACT_DELIVERED,
                input_summary=json.dumps(request.payload, ensure_ascii=False)[:1000],
                output_summary=response.raw_text[:1000],
                resource_usage=usage,
                correlation_id=correlation_id,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            return response, run

        duration_ms = int((perf_counter() - clock) * 1000)
        error_message = str(last_error).strip() if last_error else ""
        if not error_message:
            error_message = (
                type(last_error).__name__
                if last_error is not None
                else "Unknown provider error"
            )
        failed_run = AgentRun(
            project_id=request.project_id,
            work_item_id=request.work_item_id,
            agent_role=role,
            model=definition.model,
            provider=definition.provider,
            attempt=request.attempt,
            outcome=RunOutcome.BLOCKED,
            input_summary=json.dumps(request.payload, ensure_ascii=False)[:1000],
            output_summary="",
            resource_usage=ResourceUsage(
                duration_ms=duration_ms,
                queue_wait_ms=total_queue_wait_ms,
                generation_ms=total_generation_ms if generation_started else None,
                model=definition.model,
                provider=definition.provider,
                errors=errors,
            ),
            correlation_id=correlation_id or new_id(),
            error=error_message,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
        raise RoleExecutionError(error_message, failed_run) from last_error


class RoleExecutionError(RuntimeError):
    def __init__(self, message: str, run: AgentRun) -> None:
        super().__init__(message)
        self.run = run
