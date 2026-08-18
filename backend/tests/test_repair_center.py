from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agentarium.api.app import create_app
from agentarium.domain.enums import (
    AgentRole,
    ReviewVerdict,
    RunOutcome,
    VerificationMode,
    WorkItemStatus,
)
from agentarium.domain.models import (
    AgentRun,
    Artifact,
    Milestone,
    ResourceUsage,
    Review,
    TestReport,
    WorkItem,
)
from agentarium.orchestration import Orchestrator
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient
from typer.testing import CliRunner

# P4.3a -- ApplicationService.repair_center() is pure read-aggregation
# over already-persisted state (no orchestrator call, no new migration);
# these tests build work items directly in each target status/shape
# rather than driving a real run, mirroring test_manual_retry.py's own
# style for exactly the same reason: the cause taxonomy needs precise,
# reproducible input states, not whatever a mock-provider run happens to
# produce.


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


# -- service level -----------------------------------------------------------


def test_repair_center_is_empty_when_nothing_needs_repair(service: ApplicationService) -> None:
    project = service.create_project("Proyecto sano sin nada que reparar")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    service.repository.add_work_item(_work_item(project.id, milestone.id))

    assert service.repair_center() == []


def test_repair_center_includes_failed_with_last_error(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con una tarea fallida")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id, milestone.id, status=WorkItemStatus.FAILED, last_error="El build fallo"
    )
    service.repository.add_work_item(item)

    rows = service.repair_center()

    assert len(rows) == 1
    assert rows[0]["work_item_id"] == item.id
    assert rows[0]["cause"] == "failed"
    assert rows[0]["last_error"] == "El build fallo"
    assert rows[0]["project_id"] == project.id
    assert rows[0]["project_title"] == project.title
    assert rows[0]["blocking_dependency_id"] is None


def test_repair_center_includes_changes_requested(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con cambios solicitados")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.CHANGES_REQUESTED)
    service.repository.add_work_item(item)

    rows = service.repair_center()

    assert len(rows) == 1
    assert rows[0]["cause"] == "changes_requested"


def test_repair_center_includes_ready_with_exhausted_budget(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con presupuesto agotado")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id,
        milestone.id,
        status=WorkItemStatus.READY,
        max_attempts=1,
        attempt_count=1,
    )
    service.repository.add_work_item(item)

    rows = service.repair_center()

    assert len(rows) == 1
    assert rows[0]["cause"] == "exhausted"
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["max_attempts"] == 1
    # Nowhere near the MAX_WORK_ITEM_ATTEMPTS ceiling -- retry_work_item's
    # own extend_attempt_budget call would still succeed.
    assert rows[0]["attempt_repair_available"] is True


def test_repair_center_marks_attempt_repair_unavailable_at_the_25_attempt_ceiling(
    service: ApplicationService,
) -> None:
    """The literal boundary retry_work_item/extend_attempt_budget enforce:
    max_attempts already at MAX_WORK_ITEM_ATTEMPTS (25) and exhausted means
    the next retry/recover/candidate call would raise an uncaught
    ValueError ("Task attempt budget cannot exceed 25") -- the UI must
    never offer those three actions in that state."""
    project = service.create_project("Proyecto en el tope real de intentos")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id,
        milestone.id,
        status=WorkItemStatus.FAILED,
        max_attempts=25,
        attempt_count=25,
    )
    service.repository.add_work_item(item)

    rows = service.repair_center()

    assert len(rows) == 1
    assert rows[0]["attempt_repair_available"] is False
    with pytest.raises(ValueError, match="cannot exceed"):
        service.retry_work_item(item.id)


def test_repair_center_marks_attempt_repair_available_one_below_the_ceiling(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto a un intento del tope")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id,
        milestone.id,
        status=WorkItemStatus.FAILED,
        max_attempts=24,
        attempt_count=24,
    )
    service.repository.add_work_item(item)

    rows = service.repair_center()

    assert len(rows) == 1
    assert rows[0]["attempt_repair_available"] is True
    retried = service.retry_work_item(item.id)
    assert retried.max_attempts == 25


def test_repair_center_excludes_ready_with_remaining_budget(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con presupuesto restante")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id,
        milestone.id,
        status=WorkItemStatus.READY,
        max_attempts=3,
        attempt_count=1,
    )
    service.repository.add_work_item(item)

    assert service.repair_center() == []


def test_repair_center_includes_blocked_on_a_failed_dependency(service: ApplicationService) -> None:
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

    rows = service.repair_center()

    blocked_rows = [row for row in rows if row["work_item_id"] == blocked.id]
    assert len(blocked_rows) == 1
    assert blocked_rows[0]["cause"] == "blocked"
    assert blocked_rows[0]["blocking_dependency_id"] == dependency.id
    assert blocked_rows[0]["blocking_dependency_title"] == "Dependencia"
    assert blocked_rows[0]["blocking_dependency_status"] == "failed"
    # Blocked is never actionable regardless of its own attempt budget --
    # attempt_count=0/max_attempts=3 here (nowhere near exhausted), so a
    # naive "only check exhaustion" computation would wrongly say True.
    # retry_work_item rejects any non-FAILED/CHANGES_REQUESTED/exhausted-
    # READY status outright, independent of budget.
    assert blocked_rows[0]["attempt_repair_available"] is False
    # The FAILED dependency itself is also its own row, on its own cause.
    dependency_rows = [row for row in rows if row["work_item_id"] == dependency.id]
    assert len(dependency_rows) == 1
    assert dependency_rows[0]["cause"] == "failed"


def test_repair_center_excludes_blocked_on_a_healthy_dependency(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con dependencia sana")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    dependency = _work_item(
        project.id, milestone.id, title="Dependencia", status=WorkItemStatus.READY
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

    assert service.repair_center() == []


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


def test_repair_center_surfaces_latest_review_and_test_evidence(
    service: ApplicationService,
) -> None:
    project = service.create_project("Proyecto con evidencia real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED)
    service.repository.add_work_item(item)
    run = _agent_run(project.id, item.id)
    service.repository.add_agent_run(run)
    artifact = Artifact(
        project_id=project.id,
        work_item_id=item.id,
        agent_run_id=run.id,
        artifact_type="code",
        title="Artefacto",
        content={},
    )
    service.repository.add_artifact(artifact)
    service.repository.add_review(
        Review(
            project_id=project.id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            reviewer_run_id=run.id,
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            reasons=["Falta un caso de borde"],
            acceptance_results={"Existe": False},
        )
    )
    service.repository.add_test_report(
        TestReport(
            project_id=project.id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            tester_run_id=run.id,
            passed=False,
            verification_mode=VerificationMode.STATIC_ONLY,
            checks=[],
            summary="Dos pruebas fallaron",
        )
    )

    rows = service.repair_center()

    assert rows[0]["latest_review_reasons"] == ["Falta un caso de borde"]
    assert rows[0]["latest_test_summary"] == "Dos pruebas fallaron"


def test_repair_center_spans_multiple_projects_in_one_call(service: ApplicationService) -> None:
    first = service.create_project("Primer proyecto con una tarea rota")
    first_milestone = _milestone(first.id)
    service.repository.add_milestone(first_milestone)
    service.repository.add_work_item(
        _work_item(first.id, first_milestone.id, status=WorkItemStatus.FAILED)
    )
    second = service.create_project("Segundo proyecto con otra tarea rota")
    second_milestone = _milestone(second.id)
    service.repository.add_milestone(second_milestone)
    service.repository.add_work_item(
        _work_item(second.id, second_milestone.id, status=WorkItemStatus.FAILED)
    )

    rows = service.repair_center()

    assert {row["project_id"] for row in rows} == {first.id, second.id}


def test_repair_center_sorts_by_updated_at_descending(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con dos tareas rotas")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    older = _work_item(
        project.id,
        milestone.id,
        title="Vieja",
        status=WorkItemStatus.FAILED,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service.repository.add_work_item(older)
    newer = _work_item(
        project.id,
        milestone.id,
        title="Nueva",
        status=WorkItemStatus.FAILED,
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    service.repository.add_work_item(newer)
    # Explicit, deterministic timestamps -- two real-clock constructor
    # calls a few instructions apart are not a reliable ordering signal
    # on every platform/clock resolution.

    rows = service.repair_center()

    assert [row["work_item_id"] for row in rows] == [newer.id, older.id]


# -- API level -----------------------------------------------------------------


def test_repair_center_endpoint_happy_path(service: ApplicationService) -> None:
    project = service.create_project("Proyecto con una tarea fallida real")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.FAILED, last_error="boom")
    service.repository.add_work_item(item)

    app = create_app(service)
    with TestClient(app) as client:
        response = client.get("/api/repair-center")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["work_item_id"] == item.id
        assert body[0]["cause"] == "failed"


# -- CLI level -------------------------------------------------------------
#
# Each command is otherwise a thin wrapper around an ApplicationService
# method already covered elsewhere (test_manual_retry.py for eligibility,
# the service-level tests above for repair_center() itself) -- these only
# cover the CLI plumbing: argument parsing, the right service call, JSON
# output, and repair candidate's file-based payload handling specifically.


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


def _cli_create_project_with_failed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Builds state directly against the same DB the CLI will open, same
    technique test_export_project_service.py's CLI section already
    established for this codebase -- returns (project_id, work_item_id)."""
    from agentarium.cli import _service

    service = _service()
    project = service.create_project("Proyecto CLI con una tarea fallida")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(
        project.id, milestone.id, status=WorkItemStatus.FAILED, last_error="boom"
    )
    service.repository.add_work_item(item)
    return project.id, item.id


def test_repair_cli_list_shows_a_real_failed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["repair", "list"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert any(row["work_item_id"] == item_id and row["cause"] == "failed" for row in rows)


def test_repair_cli_retry_transitions_the_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["repair", "retry", item_id])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "ready"


def test_repair_cli_recover_calls_the_orchestrator_with_the_right_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    async def fake_recover(self: Orchestrator, work_item_id: str, artifact_id: str) -> WorkItem:
        captured["work_item_id"] = work_item_id
        captured["artifact_id"] = artifact_id
        return self.repository.get_work_item(work_item_id)

    monkeypatch.setattr(Orchestrator, "re_evaluate_artifact", fake_recover)

    result = CliRunner().invoke(app, ["repair", "recover", item_id, "artifact-1"])

    assert result.exit_code == 0, result.output
    assert captured == {"work_item_id": item_id, "artifact_id": "artifact-1"}


def test_repair_cli_rework_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import _service, app

    _cli_env(tmp_path, monkeypatch)
    service = _service()
    project = service.create_project("Proyecto CLI con una tarea completa")
    milestone = _milestone(project.id)
    service.repository.add_milestone(milestone)
    item = _work_item(project.id, milestone.id, status=WorkItemStatus.COMPLETED)
    service.repository.add_work_item(item)

    result = CliRunner().invoke(app, ["repair", "rework", item.id, "Falta un caso de borde"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dependency_ids"] == [item.id]
    assert payload["status"] == "ready"


def test_repair_cli_escalate_creates_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["repair", "escalate", item_id, "Necesita una decision"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["work_item_id"] == item_id
    assert payload["status"] == "pending"


def test_repair_cli_candidate_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    async def fake_evaluate(self: Orchestrator, work_item_id: str, proposal: object) -> WorkItem:
        captured["work_item_id"] = work_item_id
        captured["proposal"] = proposal
        return self.repository.get_work_item(work_item_id)

    monkeypatch.setattr(Orchestrator, "evaluate_operator_candidate", fake_evaluate)
    payload_file = tmp_path / "candidate.json"
    payload_file.write_text(
        json.dumps(
            {
                "title": "Arreglo manual",
                "summary": "Arreglo enviado por un operador",
                "files": [
                    {"path": "fix.py", "content": "print('ok')", "purpose": "Arreglo"}
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["repair", "candidate", item_id, str(payload_file)])

    assert result.exit_code == 0, result.output
    assert captured["work_item_id"] == item_id


def test_repair_cli_candidate_rejects_malformed_json_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)
    payload_file = tmp_path / "candidate.json"
    payload_file.write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(app, ["repair", "candidate", item_id, str(payload_file)])

    assert result.exit_code == 2
    assert "no es JSON" in result.output


def test_repair_cli_candidate_rejects_a_payload_that_fails_schema_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    _project_id, item_id = _cli_create_project_with_failed_item(tmp_path, monkeypatch)
    payload_file = tmp_path / "candidate.json"
    payload_file.write_text(json.dumps({"title": "Sin archivos"}), encoding="utf-8")

    result = CliRunner().invoke(app, ["repair", "candidate", item_id, str(payload_file)])

    assert result.exit_code == 2
    assert "no tiene la forma esperada" in result.output
