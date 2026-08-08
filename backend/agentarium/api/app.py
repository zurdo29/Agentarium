from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from agentarium.domain.enums import ApprovalStatus
from agentarium.execution import PreviewUnavailable, WorkspacePreview
from agentarium.isolation import ImportSourceError
from agentarium.repositories.repository import NotFoundError
from agentarium.services import ApplicationService, build_application

from .schemas import (
    CreateApprovalRequest,
    CreateProjectRequest,
    EscalateRequest,
    ImportProjectRequest,
    InspectImportRequest,
    PriorityRequest,
    ProviderSelectionRequest,
    ResolveApprovalRequest,
    ReworkRequest,
    RuntimeCapabilities,
    RuntimeStatus,
    SubmitCandidateRequest,
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

    @api.exception_handler(PreviewUnavailable)
    async def preview_unavailable_handler(_: Request, exc: PreviewUnavailable) -> Any:
        return _error_response(404, str(exc))

    @api.exception_handler(ImportSourceError)
    async def import_source_error_handler(_: Request, exc: ImportSourceError) -> Any:
        return _error_response(409, str(exc))

    @api.get("/api/health")
    async def health() -> dict[str, Any]:
        runtime = _runtime_status(resolved_service)
        return {
            "status": "ok",
            "provider": runtime.providers[0] if len(runtime.providers) == 1 else "mixed",
            "runtime": runtime.model_dump(mode="json"),
            "scheduler": resolved_service.scheduler.snapshot(),
            "database": resolved_service.database.engine.dialect.name,
        }

    @api.get("/api/dashboard")
    async def dashboard() -> dict[str, Any]:
        runtime = _runtime_status(resolved_service)
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
            "runtime": runtime.model_dump(mode="json"),
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

    @api.post("/api/projects/import/inspect")
    async def inspect_import_source(body: InspectImportRequest) -> dict[str, Any]:
        # Deliberately always 200: "doesn't exist", "dirty", "not git",
        # "has submodules", "overlaps the workspace" are all expected
        # inspection outcomes, not server errors -- only /import (which
        # actually commits to copying something) raises for those.
        inspection = await resolved_service.inspect_import_source(body.source_path)
        return asdict(inspection)

    @api.post("/api/projects/import", status_code=201)
    async def import_project(body: ImportProjectRequest) -> dict[str, Any]:
        project = await resolved_service.import_project(
            body.source_path, body.goal, body.title
        )
        return resolved_service.project_detail(project.id)

    @api.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        return resolved_service.project_detail(project_id)

    @api.get("/api/projects/{project_id}/preview", include_in_schema=False)
    async def redirect_project_preview(project_id: str) -> RedirectResponse:
        resolved_service.repository.get_project(project_id)
        return RedirectResponse(
            url=f"/api/projects/{project_id}/preview/",
            status_code=307,
        )

    @api.get("/api/projects/{project_id}/preview/", include_in_schema=False)
    async def project_preview_entry(project_id: str) -> FileResponse:
        resolved_service.repository.get_project(project_id)
        return _preview_response(resolved_service.preview.resolve(project_id))

    @api.get(
        "/api/projects/{project_id}/preview/{file_path:path}",
        include_in_schema=False,
    )
    async def project_preview_file(project_id: str, file_path: str) -> FileResponse:
        resolved_service.repository.get_project(project_id)
        return _preview_response(
            resolved_service.preview.resolve(project_id, file_path)
        )

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

    @api.post("/api/work-items/{work_item_id}/recover/{artifact_id}")
    async def recover_work_item_artifact(
        work_item_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        return (
            await resolved_service.recover_artifact(work_item_id, artifact_id)
        ).model_dump(mode="json")

    @api.post("/api/work-items/{work_item_id}/candidate")
    async def submit_work_item_candidate(
        work_item_id: str,
        body: SubmitCandidateRequest,
    ) -> dict[str, Any]:
        return (
            await resolved_service.submit_candidate(
                work_item_id,
                title=body.title,
                summary=body.summary,
                files=[
                    file.model_dump(mode="json")
                    for file in body.files
                ],
            )
        ).model_dump(mode="json")

    @api.post("/api/work-items/{work_item_id}/rework", status_code=201)
    async def rework_work_item(
        work_item_id: str,
        body: ReworkRequest,
    ) -> dict[str, Any]:
        return resolved_service.rework_work_item(
            work_item_id,
            body.reason,
            body.acceptance_criteria,
        ).model_dump(mode="json")

    @api.patch("/api/work-items/{work_item_id}/priority")
    async def update_priority(work_item_id: str, body: PriorityRequest) -> dict[str, Any]:
        return resolved_service.repository.update_priority(work_item_id, body.priority).model_dump(
            mode="json"
        )

    @api.post("/api/work-items/{work_item_id}/escalate")
    async def escalate_work_item(work_item_id: str, body: EscalateRequest) -> dict[str, Any]:
        return resolved_service.escalate_work_item(work_item_id, body.reason).model_dump(
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

    @api.get("/api/providers")
    async def providers() -> dict[str, Any]:
        return await resolved_service.provider_status()

    @api.put("/api/runtime/provider")
    async def select_provider(body: ProviderSelectionRequest) -> dict[str, Any]:
        return await resolved_service.select_provider(body.provider, body.model)

    return api


def _runtime_status(service: ApplicationService) -> RuntimeStatus:
    providers = sorted({definition.provider for definition in service.roles.all()})
    model_inference = any(provider != "mock" for provider in providers)
    return RuntimeStatus(
        mode="workspace",
        providers=providers,
        active_model=service.roles.active_model(),
        capabilities=RuntimeCapabilities(
            model_inference=model_inference,
            project_files=True,
            command_execution=True,
            change_isolation=True,
        ),
    )


def _error_response(status: int, detail: str) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"detail": detail})


def _preview_response(path: Path) -> FileResponse:
    return FileResponse(
        path,
        media_type=WorkspacePreview.media_type(path),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "sandbox allow-scripts allow-same-origin; "
                "default-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "media-src 'self' data: blob:; "
                "connect-src 'none'; object-src 'none'; "
                "base-uri 'none'; form-action 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _recovery_event(project_id: str, recovered: int) -> Any:
    from agentarium.domain.models import ExecutionEvent

    return ExecutionEvent(
        project_id=project_id,
        action="execution_recovered",
        message=f"Se recuperaron {recovered} tareas interrumpidas.",
    )


app = create_app()
