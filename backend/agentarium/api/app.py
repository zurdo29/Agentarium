from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agentarium.domain.enums import ApprovalStatus
from agentarium.repositories.repository import NotFoundError
from agentarium.services import ApplicationService, build_application

from .schemas import (
    CreateApprovalRequest,
    CreateProjectRequest,
    PriorityRequest,
    ResolveApprovalRequest,
)


def create_app(service: ApplicationService | None = None) -> FastAPI:
    resolved_service = service or build_application()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        recovered = resolved_service.initialize()
        if recovered:
            for project in resolved_service.repository.list_projects():
                resolved_service.repository.add_event(_recovery_event(project.id, recovered))
        yield
        resolved_service.database.dispose()

    api = FastAPI(
        title="Agentarium API",
        version="0.1.0",
        description="Local-first orchestration with structured, verifiable artifacts.",
        lifespan=lifespan,
    )
    api.state.service = resolved_service
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            resolved_service.settings.frontend_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> Any:
        return _error_response(404, str(exc))

    @api.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> Any:
        return _error_response(409, str(exc))

    @api.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": resolved_service.settings.provider,
            "scheduler": resolved_service.scheduler.snapshot(),
            "database": resolved_service.database.engine.dialect.name,
        }

    @api.get("/api/dashboard")
    async def dashboard() -> dict[str, Any]:
        projects = resolved_service.repository.list_projects()
        pending = resolved_service.repository.list_approvals(status=ApprovalStatus.PENDING)
        details = []
        blocked = 0
        errors: list[dict[str, Any]] = []
        for project in projects:
            items = resolved_service.repository.list_work_items(project.id)
            blocked += sum(item.status.value == "blocked" for item in items)
            project_events = resolved_service.repository.list_events(project.id, limit=30)
            errors.extend(event for event in project_events if event["error"])
            details.append(
                {
                    **project.model_dump(mode="json"),
                    "tasks_total": len(items),
                    "tasks_completed": sum(item.status.value == "completed" for item in items),
                }
            )
        return {
            "projects": details,
            "active_agents": resolved_service.scheduler.active,
            "blocked_tasks": blocked,
            "pending_approvals": len(pending),
            "latest_errors": sorted(errors, key=lambda event: event["timestamp"], reverse=True)[:8],
        }

    @api.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        return [
            project.model_dump(mode="json")
            for project in resolved_service.repository.list_projects()
        ]

    @api.post("/api/projects", status_code=201)
    async def create_project(body: CreateProjectRequest) -> dict[str, Any]:
        project = resolved_service.create_project(body.goal, body.title)
        if body.auto_plan:
            project = await resolved_service.plan_project(project.id)
        return resolved_service.project_detail(project.id)

    @api.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        return resolved_service.project_detail(project_id)

    @api.post("/api/projects/{project_id}/plan")
    async def plan_project(project_id: str) -> dict[str, Any]:
        await resolved_service.plan_project(project_id)
        return resolved_service.project_detail(project_id)

    @api.post("/api/projects/{project_id}/run")
    async def run_project(project_id: str) -> dict[str, Any]:
        await resolved_service.run_project(project_id)
        return resolved_service.project_detail(project_id)

    @api.post("/api/projects/{project_id}/pause")
    async def pause_project(project_id: str) -> dict[str, Any]:
        return resolved_service.pause_project(project_id).model_dump(mode="json")

    @api.post("/api/projects/{project_id}/resume")
    async def resume_project(project_id: str) -> dict[str, Any]:
        return resolved_service.resume_project(project_id).model_dump(mode="json")

    @api.post("/api/projects/{project_id}/cancel")
    async def cancel_project(project_id: str) -> dict[str, Any]:
        return resolved_service.cancel_project(project_id).model_dump(mode="json")

    @api.get("/api/work-items/{work_item_id}")
    async def get_work_item(work_item_id: str) -> dict[str, Any]:
        return resolved_service.repository.get_work_item(work_item_id).model_dump(mode="json")

    @api.post("/api/work-items/{work_item_id}/retry")
    async def retry_work_item(work_item_id: str) -> dict[str, Any]:
        return resolved_service.retry_work_item(work_item_id).model_dump(mode="json")

    @api.patch("/api/work-items/{work_item_id}/priority")
    async def update_priority(work_item_id: str, body: PriorityRequest) -> dict[str, Any]:
        return resolved_service.repository.update_priority(work_item_id, body.priority).model_dump(
            mode="json"
        )

    @api.get("/api/approvals")
    async def list_approvals(
        project_id: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[dict[str, Any]]:
        return [
            approval.model_dump(mode="json")
            for approval in resolved_service.repository.list_approvals(project_id, status)
        ]

    @api.post("/api/approvals", status_code=201)
    async def create_approval(body: CreateApprovalRequest) -> dict[str, Any]:
        approval = resolved_service.request_approval(
            body.project_id,
            body.action,
            body.reason,
            work_item_id=body.work_item_id,
            affected_resources=body.affected_resources,
        )
        return approval.model_dump(mode="json")

    @api.post("/api/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, body: ResolveApprovalRequest) -> dict[str, Any]:
        approval = resolved_service.repository.resolve_approval(
            approval_id, body.status, body.comments
        )
        return approval.model_dump(mode="json")

    @api.get("/api/projects/{project_id}/events")
    async def list_events(
        project_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=250, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        resolved_service.repository.get_project(project_id)
        return resolved_service.repository.list_events(
            project_id, after_sequence=after_sequence, limit=limit
        )

    @api.get("/api/projects/{project_id}/events/stream")
    async def event_stream(
        project_id: str,
        after_sequence: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        resolved_service.repository.get_project(project_id)

        async def generate() -> AsyncIterator[str]:
            cursor = after_sequence
            while True:
                events = resolved_service.repository.list_events(
                    project_id, after_sequence=cursor, limit=100
                )
                for event in events:
                    cursor = max(cursor, int(event["sequence"]))
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/api/projects/{project_id}/metrics")
    async def project_metrics(project_id: str) -> dict[str, Any]:
        return resolved_service.repository.metrics(project_id)

    @api.get("/api/agents")
    async def agents() -> dict[str, Any]:
        return {
            "definitions": [
                definition.model_dump(mode="json") for definition in resolved_service.roles.all()
            ],
            "scheduler": resolved_service.scheduler.snapshot(),
        }

    return api


def _error_response(status: int, detail: str) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"detail": detail})


def _recovery_event(project_id: str, recovered: int) -> Any:
    from agentarium.domain.models import ExecutionEvent

    return ExecutionEvent(
        project_id=project_id,
        action="execution_recovered",
        message=f"Se recuperaron {recovered} tareas interrumpidas.",
    )


app = create_app()
