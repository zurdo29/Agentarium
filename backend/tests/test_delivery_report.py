from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentarium.api.app import create_app
from agentarium.domain.enums import AgentRole, ReviewVerdict, RunOutcome, WorkItemStatus
from agentarium.domain.models import (
    AgentRun,
    ApprovalRequest,
    Artifact,
    ExecutionEvent,
    Milestone,
    ProjectBrief,
    ResourceUsage,
    Review,
    TestReport,
    WorkItem,
)
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient
from typer.testing import CliRunner

# P4.4a -- ApplicationService.delivery_report() is pure read-aggregation,
# same style/reasoning as test_repair_center.py: build state directly in
# each target shape rather than driving a real run, since the outcome
# taxonomy needs precise, reproducible input states.


def _milestone(project_id: str) -> Milestone:
    return Milestone(project_id=project_id, title="Entrega", description="Entrega local", order=0)


def _work_item(project_id: str, milestone_id: str, **overrides: object) -> WorkItem:
    defaults: dict[str, object] = {
        "project_id": project_id,
        "milestone_id": milestone_id,
        "title": "Tarea",
        "description": "Descripcion de la tarea",
        "expected_outputs": ["resultado"],
        "acceptance_criteria": ["Existe"],
        "max_attempts": 3,
        "status": WorkItemStatus.READY,
    }
    defaults.update(overrides)
    return WorkItem(**defaults)  # type: ignore[arg-type]


def _agent_run(project_id: str, work_item_id: str) -> AgentRun:
    return AgentRun(
        project_id=project_id,
        work_item_id=work_item_id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="fake-model",
        provider="fake",
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="{}",
        output_summary="done",
        resource_usage=ResourceUsage(duration_ms=1, model="fake-model", provider="fake"),
        correlation_id="corr-1",
    )


def _completed_with_evidence(
    service: ApplicationService, project_id: str, item: WorkItem
) -> None:
    """A completed item built directly (not via a real run) still needs a
    real review+test_report to look like one that went through the real
    COMPLETED gate (engine.py:1115) -- otherwise it would trip the
    unverified_completed_items safety net by construction, not by the
    thing the test actually wants to check. artifacts/reviews/test_reports
    all carry real FK columns into agent_runs, so a fabricated run id
    would violate the schema (same pitfall test_repair_center.py's own
    evidence test already found in P4.3a) -- a real AgentRun row first."""
    run = _agent_run(project_id, item.id)
    service.repository.add_agent_run(run)
    artifact = Artifact(
        project_id=project_id,
        work_item_id=item.id,
        agent_run_id=run.id,
        artifact_type="code",
        title="Artefacto",
        content={},
    )
    service.repository.add_artifact(artifact)
    service.repository.add_review(
        Review(
            project_id=project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            reviewer_run_id=run.id,
            verdict=ReviewVerdict.APPROVED,
            reasons=["Todo bien"],
            acceptance_results={"Existe": True},
        )
    )
    service.repository.add_test_report(
        TestReport(
            project_id=project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            tester_run_id=run.id,
            passed=True,
            checks=[{"name": "existe", "passed": True}],
            summary="Todo paso",
        )
    )


# -- service level -------------------------------------------------------------


def test_delivery_report_is_all_zero_for_an_empty_project(service: ApplicationService) -> None:
    project = service.create_project("Proyecto vacio")

    report = service.delivery_report(project.id)

    assert report["work_items"] == []
    assert report["unverified_completed_items"] == []
    assert report["totals"] == {}
    assert report["project"]["id"] == project.id
    assert report["project"]["goal"] == project.goal


def test_delivery_report_includes_the_project_brief(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con brief real")
    service.repository.set_project_brief(
        ProjectBrief(
            project_id=project.id,
            summary="Resumen real",
            scope=["Alcance A"],
            deliverables=["Entregable A"],
            assumptions=[],
            ambiguities=[],
            constraints=[],
            success_criteria=["Se completa"],
        )
    )

    report = service.delivery_report(project.id)

    assert report["project"]["brief"]["summary"] == "Resumen real"


def test_delivery_report_marks_a_real_completed_item(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con una entrega real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.COMPLETED)
    service.repository.add_work_item(item)
    _completed_with_evidence(service, project.id, item)

    report = service.delivery_report(project.id)

    row = report["work_items"][0]
    assert row["outcome"] == "completed"
    assert row["review_verdict"] == "approved"
    assert row["review_acceptance_results"] == {"Existe": True}
    assert row["test_passed"] is True
    assert report["unverified_completed_items"] == []
    assert report["totals"] == {"completed": 1}


def test_delivery_report_flags_a_completed_item_missing_evidence(
    service: ApplicationService,
) -> None:
    """Not reachable via any real code path (engine.py's COMPLETED gate
    always requires a passing test_report + an APPROVED review) --
    exercised here only by bypassing that gate directly through the
    repository, the same way a hand-edited DB could produce this."""
    project = service.create_project("Proyecto con integridad rota")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.COMPLETED)
    service.repository.add_work_item(item)

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "completed"
    assert report["unverified_completed_items"] == [
        {"work_item_id": item.id, "title": item.title}
    ]


@pytest.mark.parametrize(
    "status,expected_outcome",
    [
        (WorkItemStatus.FAILED, "failed"),
        (WorkItemStatus.CHANGES_REQUESTED, "changes_requested"),
    ],
)
def test_delivery_report_matches_repair_centers_own_cause(
    service: ApplicationService, status: WorkItemStatus, expected_outcome: str
) -> None:
    project = service.create_project("Proyecto con una tarea atascada")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=status)
    service.repository.add_work_item(item)

    report = service.delivery_report(project.id)
    repair_rows = service.repair_center()

    assert report["work_items"][0]["outcome"] == expected_outcome
    # Cross-consistency: the two readers share _classify_stuck_work_items,
    # so they can never disagree on the same item's cause.
    assert report["work_items"][0]["outcome"] == repair_rows[0]["cause"]


def test_delivery_report_matches_repair_center_for_a_blocked_item(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con dependencia rota")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    dependency = _work_item(
        project.id, milestone.id, title="Dependencia", status=WorkItemStatus.FAILED
    )
    service.repository.add_work_item(dependency)
    blocked = _work_item(
        project.id,
        milestone.id,
        title="Bloqueada",
        status=WorkItemStatus.BLOCKED,
        dependency_ids=[dependency.id],
    )
    service.repository.add_work_item(blocked)

    report = service.delivery_report(project.id)
    repair_rows = {row["work_item_id"]: row for row in service.repair_center()}

    blocked_row = next(row for row in report["work_items"] if row["work_item_id"] == blocked.id)
    assert blocked_row["outcome"] == "blocked"
    assert blocked_row["blocking_dependency_id"] == dependency.id
    assert blocked_row["outcome"] == repair_rows[blocked.id]["cause"]


def test_delivery_report_marks_a_cancelled_item(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con una tarea cancelada")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.CANCELLED)
    service.repository.add_work_item(item)

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "cancelled"


def test_delivery_report_marks_a_split_parent_as_superseded_not_cancelled(
    service: ApplicationService,
) -> None:
    """Correction 2: a parent cancelled by a real task_split_created event
    is a fundamentally different fact than a plain cancellation -- its
    work was redistributed into children, not lost."""
    project = service.create_project("Proyecto con una tarea dividida")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    parent = _work_item(project.id, milestone.id, status=WorkItemStatus.CANCELLED)
    service.repository.add_work_item(parent)
    service.repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=parent.id,
            action="task_split_created",
            message=f"{parent.title} se dividio en subtareas.",
            metadata={"child_ids": ["child-1", "child-2"], "consolidation_id": "cons-1"},
        )
    )

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "superseded_by_split"
    assert report["totals"] == {"superseded_by_split": 1}


def test_delivery_report_derives_awaiting_approval_from_the_real_approval_link(
    service: ApplicationService,
) -> None:
    """Correction 3: WorkItemStatus.AWAITING_APPROVAL is declared in the
    state machine but no code path ever assigns it to a work item --
    escalate_work_item only ever changes the project's status. Checking
    item.status here would match nothing, ever, in any real project.
    The item is left FAILED on purpose, to prove the check does not
    depend on that field."""
    project = service.create_project("Proyecto con una escalacion real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)
    service.escalate_work_item(item.id, "Necesita una decision humana real.")

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["status"] == "failed"
    assert report["work_items"][0]["outcome"] == "awaiting_approval"


def test_delivery_report_ignores_a_pending_approval_with_no_real_work_item_link(
    service: ApplicationService,
) -> None:
    """A generic approval (no work_item_id at all) must never accidentally
    match any item -- same structural-link discipline as resolve_approval
    (ADR 0037)."""
    project = service.create_project("Proyecto con una aprobacion generica")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)
    service.repository.add_approval(
        ApprovalRequest(
            project_id=project.id,
            work_item_id=None,
            action="Decision generica",
            reason="No relacionada a ningun work item.",
            risk="high",
            alternatives=[],
            affected_resources=[],
        )
    )

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "failed"


def test_delivery_report_ignores_a_pending_approval_with_a_coincidental_work_item_id(
    service: ApplicationService,
) -> None:
    """Stronger than the test above: this approval's own work_item_id
    DOES point at a real item -- but with no task_escalated event to
    back it, that alone must not be enough. approval.work_item_id could
    carry one incidentally on a generic, non-escalation approval; only a
    real task_escalated event naming this exact approval counts (same
    structural-link discipline as resolve_approval, ADR 0037)."""
    project = service.create_project("Proyecto con una aprobacion coincidente")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)
    service.repository.add_approval(
        ApprovalRequest(
            project_id=project.id,
            work_item_id=item.id,
            action="Decision generica que menciona el mismo work item",
            reason="No nacio de una escalacion real.",
            risk="high",
            alternatives=[],
            affected_resources=[],
        )
    )

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "failed"


def test_delivery_report_marks_a_running_item_in_progress(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con una tarea en curso")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.RUNNING)
    service.repository.add_work_item(item)

    report = service.delivery_report(project.id)

    assert report["work_items"][0]["outcome"] == "in_progress"


def test_delivery_report_includes_real_integration_facts(service: ApplicationService) -> None:
    """Correction 1: integration_commit/branch/files come from a real
    change_set_integrated event, not inferred from status alone."""
    project = service.create_project("Proyecto con una integracion real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.COMPLETED)
    service.repository.add_work_item(item)
    _completed_with_evidence(service, project.id, item)
    service.repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=item.id,
            action="change_set_integrated",
            message="Cambios integrados.",
            metadata={
                "branch": f"task/{item.id}",
                "candidate_commit": "c" * 40,
                "integration_commit": "b" * 40,
                "files": ["result.py"],
            },
        )
    )

    report = service.delivery_report(project.id)

    row = report["work_items"][0]
    assert row["integration_commit"] == "b" * 40
    assert row["integration_branch"] == f"task/{item.id}"
    assert row["integration_files"] == ["result.py"]


def test_delivery_report_totals_a_mixed_project(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con resultados mixtos")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    completed = _work_item(
        project.id, milestone.id, title="Completa", status=WorkItemStatus.COMPLETED
    )
    service.repository.add_work_item(completed)
    _completed_with_evidence(service, project.id, completed)
    service.repository.add_work_item(
        _work_item(project.id, milestone.id, title="Fallida", status=WorkItemStatus.FAILED)
    )
    service.repository.add_work_item(
        _work_item(project.id, milestone.id, title="En curso", status=WorkItemStatus.RUNNING)
    )

    report = service.delivery_report(project.id)

    assert report["totals"] == {"completed": 1, "failed": 1, "in_progress": 1}


# -- API level -------------------------------------------------------------


def test_delivery_report_endpoint_happy_path(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con informe real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)

    app = create_app(service)
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project.id}/report")
        assert response.status_code == 200
        body = response.json()
        assert body["work_items"][0]["work_item_id"] == item.id
        assert body["work_items"][0]["outcome"] == "failed"


def test_agent_runs_endpoint_happy_path(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con corridas reales")

    app = create_app(service)
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project.id}/agent-runs")
        assert response.status_code == 200
        assert response.json() == []


def test_agent_runs_endpoint_404s_for_an_unknown_project(service: ApplicationService) -> None:
    app = create_app(service)
    with TestClient(app) as client:
        response = client.get("/api/projects/does-not-exist/agent-runs")
        assert response.status_code == 404


# -- CLI level -------------------------------------------------------------


def _cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentarium.config import settings as settings_module

    database = (tmp_path / "db.sqlite").as_posix()
    monkeypatch.setenv("AGENTARIUM_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("AGENTARIUM_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv(
        "AGENTARIUM_PROVIDER_STATE_PATH", str(tmp_path / "provider-selection.json")
    )
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr("agentarium.cli.project_root", lambda: tmp_path, raising=True)


def test_report_cli_shows_a_real_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import _service, app

    _cli_env(tmp_path, monkeypatch)
    service = _service()
    project = service.create_project("Proyecto CLI con informe real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)

    result = CliRunner().invoke(app, ["project", "report", project.id])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["work_items"][0]["work_item_id"] == item.id
    assert payload["work_items"][0]["outcome"] == "failed"
