import pytest
from agentarium.api.app import create_app
from agentarium.domain.enums import ApprovalStatus, ProjectStatus, WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_sensitive_goal_waits_for_approval(
    service: ApplicationService,
) -> None:
    project = service.create_project("Publicar el resultado y hacer push al repositorio remoto")
    approvals = service.repository.list_approvals(project.id, ApprovalStatus.PENDING)
    assert len(approvals) == 2

    planned = await service.plan_project(project.id)
    assert planned.status is ProjectStatus.AWAITING_APPROVAL

    for approval in approvals:
        service.repository.resolve_approval(
            approval.id, ApprovalStatus.APPROVED, "Autorizado para la prueba"
        )
    resumed = service.resume_project(project.id)
    assert resumed.status is ProjectStatus.READY


def test_approval_cannot_be_resolved_twice(
    service: ApplicationService,
) -> None:
    project = service.create_project("Objetivo local")
    approval = service.request_approval(project.id, "Acción protegida", "Requiere decisión humana")
    service.repository.resolve_approval(approval.id, ApprovalStatus.REJECTED, "No ejecutar")
    with pytest.raises(ValueError, match="already"):
        service.repository.resolve_approval(
            approval.id, ApprovalStatus.APPROVED, "Cambio de opinión"
        )


# -- P4.3a: ApplicationService.resolve_approval() ------------------------------
#
# The core of the user's correction: only a task_escalated event whose own
# metadata names this exact approval id authorizes a retry -- never
# approval.work_item_id alone, since a non-escalation approval could carry
# one incidentally. Each test below is a direct regression for one clause
# of that design.


def _add_work_item(service: ApplicationService, project_id: str, **overrides: object) -> WorkItem:
    milestone = Milestone(
        project_id=project_id, title="Entrega", description="Entrega local", order=0
    )
    service.repository.add_milestone(milestone)
    defaults: dict[str, object] = {
        "project_id": project_id,
        "milestone_id": milestone.id,
        "title": "Tarea",
        "description": "Descripcion de la tarea",
        "expected_outputs": ["resultado"],
        "acceptance_criteria": ["Existe"],
        "status": WorkItemStatus.FAILED,
    }
    defaults.update(overrides)
    item = WorkItem(**defaults)  # type: ignore[arg-type]
    service.repository.add_work_item(item)
    return item


def test_resolve_approval_retries_a_genuinely_escalated_failed_item_and_resumes_project(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con una tarea escalada de verdad")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)
    approval = service.escalate_work_item(item.id, "Necesita una decisión humana")

    resolved = service.resolve_approval(approval.id, ApprovalStatus.APPROVED, "Autorizado")

    assert resolved.status is ApprovalStatus.APPROVED
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
    assert service.repository.get_project(project.id).status is ProjectStatus.READY


def test_resolve_approval_ignores_a_work_item_id_on_a_non_escalation_approval(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con una aprobación genérica")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)
    # Built directly via request_approval, never escalate_work_item -- no
    # task_escalated event links this approval to the item, even though
    # the approval itself happens to carry a work_item_id.
    approval = service.request_approval(
        project.id,
        "Alguna otra acción",
        "Algún otro motivo, sin relación con una escalación",
        work_item_id=item.id,
    )

    service.resolve_approval(approval.id, ApprovalStatus.APPROVED, None)

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.FAILED


def test_resolve_approval_rejecting_an_escalation_never_retries(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con una escalación rechazada")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)
    approval = service.escalate_work_item(item.id, "Necesita una decisión humana")

    service.resolve_approval(approval.id, ApprovalStatus.REJECTED, "No autorizado")

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.FAILED


def test_resolve_approval_leaves_a_non_retryable_escalated_item_untouched(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con una escalación en curso")
    item = _add_work_item(service, project.id, status=WorkItemStatus.RUNNING)
    approval = service.escalate_work_item(item.id, "Riesgo detectado a mitad de la ejecución")

    resolved = service.resolve_approval(approval.id, ApprovalStatus.APPROVED, "Autorizado")

    assert resolved.status is ApprovalStatus.APPROVED
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.RUNNING


def test_resolve_approval_does_not_resume_project_while_another_approval_is_pending(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con dos aprobaciones pendientes")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)
    approval = service.escalate_work_item(item.id, "Necesita una decisión humana")
    service.request_approval(project.id, "Otra acción", "Otro motivo, sin resolver todavía")

    service.resolve_approval(approval.id, ApprovalStatus.APPROVED, "Autorizado")

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
    assert service.repository.get_project(project.id).status is ProjectStatus.AWAITING_APPROVAL


def test_resolve_approval_never_revives_a_cancelled_project(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto cancelado con una escalación pendiente")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)
    approval = service.escalate_work_item(item.id, "Necesita una decisión humana")
    service.repository.update_project_status(project.id, ProjectStatus.CANCELLED)

    resolved = service.resolve_approval(approval.id, ApprovalStatus.APPROVED, "Autorizado")

    assert resolved.status is ApprovalStatus.APPROVED
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
    assert service.repository.get_project(project.id).status is ProjectStatus.CANCELLED


def test_resolve_approval_endpoint_retries_a_failed_item_without_a_second_manual_step(
    service: ApplicationService,
) -> None:
    """Same auto-retry proven at the service level above, but through the
    real HTTP route -- confirms /api/approvals/{id}/resolve actually calls
    the new ApplicationService.resolve_approval, not just the repository
    method it used to call directly."""
    project = service.create_project("Proyecto con una tarea escalada por API")
    item = _add_work_item(service, project.id, status=WorkItemStatus.FAILED)

    app = create_app(service)
    with TestClient(app) as client:
        escalation = client.post(
            f"/api/work-items/{item.id}/escalate",
            json={"reason": "Necesita una decisión humana"},
        )
        assert escalation.status_code == 200

        resolved = client.post(
            f"/api/approvals/{escalation.json()['id']}/resolve",
            json={"status": "approved", "comments": "Autorizado"},
        )
        assert resolved.status_code == 200

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
    assert service.repository.get_project(project.id).status is ProjectStatus.READY
