from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from agentarium.api.app import create_app
from agentarium.domain.enums import RiskLevel, WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem
from agentarium.isolation import ImportSourceError
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient
from typer.testing import CliRunner

# test_import_source.py already proves the copy mechanism itself in
# isolation (never writes to the original, TOCTOU handling, autocrlf,
# submodules, symlinks, ...). This file proves the wiring around it: a
# real ApplicationService/API round-trip persists `imported=True` and the
# metadata correctly, and -- the point P3.4 exists for -- a work item on
# an imported project still ends up blocked by that authority gate.


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=True, shell=False
    )


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--initial-branch=main"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    _run(["git", "config", "user.email", "test@example.com"], cwd=path)


def _commit_all(path: Path, message: str) -> str:
    _run(["git", "add", "-A"], cwd=path)
    _run(["git", "commit", "-m", message], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


# -- service level -------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_project_persists_imported_true_and_metadata(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "README.md").write_text("# Repo\n", encoding="utf-8")
    head = _commit_all(source, "initial")

    project = await service.import_project(str(source), "Agregar una feature")

    reloaded = service.repository.get_project(project.id)
    assert reloaded.imported is True
    assert reloaded.imported_commit == head
    assert reloaded.imported_source_path is not None
    assert Path(reloaded.imported_source_path) == source.resolve()

    events = {event["action"] for event in service.repository.list_events(project.id)}
    assert "project_imported" in events


@pytest.mark.asyncio
async def test_import_project_execution_stays_blocked_by_authority_gate(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "README.md").write_text("# Repo\n", encoding="utf-8")
    _commit_all(source, "initial")

    project = await service.import_project(str(source), "Agregar una feature")
    milestone = Milestone(
        project_id=project.id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Entregar tool.py",
        description="Entregar una herramienta ejecutable",
        expected_outputs=["tool.py"],
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
        max_attempts=1,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)

    original_generate = MockProvider.generate

    async def controlled_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id == item.id:
            payload = {
                "artifact_type": "code",
                "title": "Herramienta",
                "summary": "Entrega una herramienta",
                "quality": "verified",
                "files": [
                    {"path": "tool.py", "content": "print('hola')\n", "purpose": "Herramienta"}
                ],
            }
            raw = json.dumps(payload)
            return ProviderResponse(
                content=payload,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_generate)

    await service.orchestrator._execute_work_item(item, "test-correlation")

    reloaded_item = service.repository.get_work_item(item.id)
    assert reloaded_item.status is WorkItemStatus.FAILED
    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "imported_project_execution_blocked" in events


@pytest.mark.asyncio
async def test_import_project_refuses_dirty_source(
    service: ApplicationService, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")
    (source / "file.txt").write_text("v2 -- uncommitted", encoding="utf-8")

    with pytest.raises(ImportSourceError):
        await service.import_project(str(source), "Agregar una feature")

    assert service.repository.list_projects() == []


# -- API level -----------------------------------------------------------------


def test_import_inspect_and_import_endpoints_happy_path(
    service: ApplicationService, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "README.md").write_text("# Repo\n", encoding="utf-8")
    head = _commit_all(source, "initial")

    app = create_app(service)
    with TestClient(app) as client:
        inspect_response = client.post(
            "/api/projects/import/inspect", json={"source_path": str(source)}
        )
        assert inspect_response.status_code == 200
        body = inspect_response.json()
        assert body["eligible"] is True
        assert body["is_git_repo"] is True
        assert body["head_commit"] == head

        import_response = client.post(
            "/api/projects/import",
            json={"source_path": str(source), "goal": "Agregar una feature"},
        )
        assert import_response.status_code == 201
        project = import_response.json()["project"]
        assert project["imported"] is True
        assert project["imported_commit"] == head


def test_import_inspect_endpoint_never_errors_for_a_missing_path(
    service: ApplicationService, tmp_path: Path
) -> None:
    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            "/api/projects/import/inspect",
            json={"source_path": str(tmp_path / "does-not-exist")},
        )
        assert response.status_code == 200
        assert response.json()["eligible"] is False


def test_import_endpoint_rejects_dirty_source_with_409_and_creates_nothing(
    service: ApplicationService, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")
    (source / "file.txt").write_text("v2 -- uncommitted", encoding="utf-8")

    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            "/api/projects/import",
            json={"source_path": str(source), "goal": "Agregar una feature"},
        )
        assert response.status_code == 409
        assert client.get("/api/projects").json() == []


def test_import_endpoint_rejects_relative_source_path(service: ApplicationService) -> None:
    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            "/api/projects/import",
            json={"source_path": "relative/path", "goal": "Agregar una feature"},
        )
        assert response.status_code == 422


# -- CLI level -------------------------------------------------------------
#
# `project import` is otherwise a thin wrapper already covered end-to-end by
# the service/API tests above -- these two only cover what is genuinely
# CLI-specific: the ImportSourceError -> typer.Exit(10) mapping (nothing
# else exercises it) and that the JSON output round-trips through stdout.


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


def test_import_cli_happy_path_persists_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "README.md").write_text("# Repo\n", encoding="utf-8")
    head = _commit_all(source, "initial")

    result = CliRunner().invoke(
        app, ["project", "import", str(source), "Agregar una feature"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["imported"] is True
    assert payload["imported_commit"] == head


def test_import_cli_rejects_dirty_source_with_exit_code_ten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")
    (source / "file.txt").write_text("v2 -- uncommitted", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["project", "import", str(source), "Agregar una feature"]
    )

    assert result.exit_code == 10
    assert "cambios sin commitear" in result.output
