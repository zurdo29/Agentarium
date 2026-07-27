from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import yaml

from agentarium.domain.enums import AgentRole, RunOutcome
from agentarium.domain.models import AgentDefinition, AgentRun, ResourceUsage, new_id
from agentarium.execution.scheduler import ResourceScheduler
from agentarium.llm import ModelRequest, ProviderRegistry, ProviderResponse


class RoleCatalog:
    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._roles: dict[AgentRole, AgentDefinition] = {}
        for role_name, config in raw["roles"].items():
            role = AgentRole(role_name)
            self._roles[role] = AgentDefinition(role=role, **config)

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
        for retry in range(definition.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.scheduler.run(lambda: provider.generate(request, definition)),
                    timeout=definition.timeout_seconds,
                )
                duration_ms = int((perf_counter() - clock) * 1000)
                usage = ResourceUsage(
                    duration_ms=duration_ms,
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
            except Exception as exc:
                errors += 1
                last_error = exc
                if retry < definition.max_retries:
                    await asyncio.sleep(min(0.1 * (2**retry), 1))
        duration_ms = int((perf_counter() - clock) * 1000)
        error_message = str(last_error) if last_error else "Unknown provider error"
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
