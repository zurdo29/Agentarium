import pytest
from agentarium.domain.enums import ApprovalStatus, ProjectStatus
from agentarium.services import ApplicationService


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
