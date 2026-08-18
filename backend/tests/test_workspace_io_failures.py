"""OSError raised by the filesystem itself must arrive classified.

The routing of ADR 0025 only works if the failures it classifies actually
reach it. `stage()` calls `mkdir`, `write_bytes`, `os.replace` and reads the
file back; every one of those can fail for reasons unrelated to the proposal,
and a bare OSError is caught by nobody — `_execute_work_item` only handles
`(InvalidPlan, WorkspaceRejected, IsolationError)`, so it would escape the
orchestrator and take the request down.

These tests drive the failure from the filesystem primitive, not by raising
the classified exception directly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.domain.enums import WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem, new_id
from agentarium.execution import (
    WorkspaceFileProposal,
    WorkspaceInfrastructureRejected,
    WorkspaceMaterializer,
    WorkspaceSecurityRejected,
)
from agentarium.services import ApplicationService
from test_attempt_exhaustion_routing import _fixed_work_response, _work_content


def _materializer(root: Path) -> WorkspaceMaterializer:
    return WorkspaceMaterializer(
        root,
        project_root() / "configs" / "policies" / "security.yaml",
    )


def _proposal(path: str = "src/main.py") -> WorkspaceFileProposal:
    return WorkspaceFileProposal(
        path=path,
        content="print('hola')\n",
        purpose="Entrada verificable",
    )


def test_a_directory_blocked_by_a_file_is_an_infrastructure_rejection(
    tmp_path: Path,
) -> None:
    # Genuine failure, nothing injected: `src` already exists as a regular
    # file, so creating `src/` as the parent directory cannot work.
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)
    (destination / "src").write_text("no soy un directorio", encoding="utf-8")

    with pytest.raises(WorkspaceInfrastructureRejected, match="could not be written"):
        materializer.stage("project", destination, [_proposal()])


def test_a_full_disk_during_the_write_is_an_infrastructure_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)

    def no_space(self, data):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", no_space)

    with pytest.raises(WorkspaceInfrastructureRejected, match="could not be written"):
        materializer.stage("project", destination, [_proposal()])


def test_a_failing_replace_is_an_infrastructure_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Not PermissionError: that one has its own documented Windows fallback.
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)

    def broken_replace(src, dst):  # type: ignore[no-untyped-def]
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(WorkspaceInfrastructureRejected, match="could not be written"):
        materializer.stage("project", destination, [_proposal()])


def test_an_unreadable_file_during_verification_is_an_infrastructure_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)
    evidence = materializer.stage("project", destination, [_proposal()])[0]
    assert materializer.verify(evidence)

    def unreadable(self):  # type: ignore[no-untyped-def]
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "read_bytes", unreadable)

    # Not `False`: a disk that cannot be read back is not the candidate's fault.
    with pytest.raises(WorkspaceInfrastructureRejected, match="could not be read back"):
        materializer.verify(evidence)


def test_a_security_rejection_is_not_reclassified_as_infrastructure(
    tmp_path: Path,
) -> None:
    # WorkspaceRejected derives from PermissionError, which IS an OSError, so
    # a careless `except OSError` in stage() would swallow the security class
    # and downgrade a sandbox escape into a disk problem.
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)

    with pytest.raises(WorkspaceSecurityRejected):
        materializer.stage(
            "project",
            tmp_path / "otro-lugar",
            [_proposal()],
        )


@pytest.mark.asyncio
async def test_an_io_failure_does_not_escape_the_orchestrator(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End to end: the run must degrade the task, not raise out of
    # _execute_work_item. Infrastructure never splits (ADR 0025).
    project = service.create_project("Objetivo con disco lleno")
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Implementar la API",
        description="Cubre dos criterios",
        expected_outputs=["library/api.py"],
        acceptance_criteria=["Listar libros", "Agregar libros"],
        max_attempts=1,
        attempt_count=0,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)
    _fixed_work_response(monkeypatch, _work_content())

    def no_space(self, data):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", no_space)

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.FAILED
    rejections = [
        event
        for event in service.repository.list_events(project.id)
        if event["action"] == "workspace_action_rejected"
    ]
    assert rejections, "el fallo de disco debe quedar registrado, no propagarse"
    assert rejections[-1]["metadata"] == {"may_retry": True, "may_split": False}
    assert not any(
        candidate.title.startswith("[subtarea] ")
        for candidate in service.repository.list_work_items(project.id)
    )


@pytest.mark.asyncio
async def test_a_verification_failure_does_not_escape_the_orchestrator(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # verify() runs after the task already moved to AWAITING_REVIEW, outside
    # the rejection routing, so it needs its own handling.
    project = service.create_project("Objetivo con verificacion rota")
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Implementar la API",
        description="Cubre dos criterios",
        expected_outputs=["library/api.py"],
        acceptance_criteria=["Listar libros", "Agregar libros"],
        max_attempts=1,
        attempt_count=0,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)
    _fixed_work_response(monkeypatch, _work_content())

    def unreadable(evidence):  # type: ignore[no-untyped-def]
        raise WorkspaceInfrastructureRejected("Workspace file could not be read back")

    monkeypatch.setattr(service.orchestrator.workspace, "verify", unreadable)

    await service.orchestrator._execute_work_item(item, new_id())

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "workspace_verification_unavailable" in events
    # Whatever the technical gate then decides is fine; what matters is that
    # the orchestrator finished handling the task instead of raising out of it.
    assert service.repository.get_work_item(item.id).status not in {
        WorkItemStatus.ASSIGNED,
        WorkItemStatus.RUNNING,
    }
