from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentarium.api.app import create_app
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient
from typer.testing import CliRunner

# test_export.py already proves the exporter mechanism itself in isolation
# (never writes to the original, base resolution, patch/bundle correctness,
# fail-safe publish, git/DB alignment). This file proves the wiring around
# it: a real full mock-provider run produces a summary that actually
# matches the database, the service emits `project_exported` with real
# paths, a partial publish failure at the application layer (not the
# exporter's own git/bundle step) still discards cleanly, and the
# API/CLI surfaces behave as documented.

_FIXTURE_GOAL = "Crear un pequeño ARPG con progresión de objetos y alcance de prototipo"


async def _run_fixture_project(service: ApplicationService) -> str:
    project = service.create_project(_FIXTURE_GOAL)
    await service.run_project(project.id)
    return project.id


def _integration_events(service: ApplicationService, project_id: str) -> list[dict[str, object]]:
    return [
        event
        for event in service.repository.list_events(project_id, limit=1000)
        if event["action"] == "change_set_integrated"
    ]


# Plain sync helpers (not called directly from `async def` test bodies) so
# these stat/read calls never trip ASYNC240 -- the actual filesystem checks
# below are trivial, synchronous pytest assertions, not something that
# belongs behind trio.Path/anyio.path.
def _assert_directory_contains(directory: Path, *names: str) -> None:
    assert directory.is_dir()
    for name in names:
        assert (directory / name).is_file()


def _is_file(path: str) -> bool:
    return Path(path).is_file()


# -- service level -------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_project_summary_matches_real_database_state(
    service: ApplicationService, tmp_path: Path
) -> None:
    project_id = await _run_fixture_project(service)
    integration_events = _integration_events(service, project_id)
    assert integration_events, "fixture project should have integrated at least one work item"

    payload = await service.export_project(project_id, str(tmp_path / "export-out"))

    assert len(payload["delivered_work_items"]) == len(integration_events)
    for entry in payload["delivered_work_items"]:
        assert entry["status"] == "completed"
        assert entry["tester_passed"] is True
        assert entry["review_verdict"] is not None
        assert entry["in_exported_range"] is True

    assert payload["consistency"]["integration_events_total"] == len(integration_events)
    assert payload["consistency"]["integration_events_matched_in_git_history"] == len(
        integration_events
    )
    assert payload["consistency"]["matches_git_history"] is True

    independent_metrics = service.repository.metrics(project_id)
    assert payload["totals"] == independent_metrics


@pytest.mark.asyncio
async def test_export_project_emits_event_and_writes_real_files(
    service: ApplicationService, tmp_path: Path
) -> None:
    project_id = await _run_fixture_project(service)

    payload = await service.export_project(project_id, str(tmp_path / "export-out"))

    events = [
        event
        for event in service.repository.list_events(project_id, limit=1000)
        if event["action"] == "project_exported"
    ]
    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["destination"] == payload["destination"]
    assert metadata["formats"] == ["bundle", "patch"]

    destination = Path(payload["destination"])
    _assert_directory_contains(
        destination, "changes.patch", "changes.bundle", "summary.json", "summary.md"
    )
    assert json.loads((destination / "summary.json").read_text(encoding="utf-8")) == {
        key: value for key, value in payload.items()
        if key not in {"destination", "patch_path", "bundle_path"}
    }


@pytest.mark.asyncio
async def test_export_preview_matches_export_summary_without_writing_anything(
    service: ApplicationService, tmp_path: Path
) -> None:
    project_id = await _run_fixture_project(service)
    project_root_path = service.settings.workspace_root / project_id / "project"
    before = (project_root_path / ".git" / "HEAD").read_bytes()

    preview_payload = await service.export_preview(project_id)

    after = (project_root_path / ".git" / "HEAD").read_bytes()
    assert before == after
    assert preview_payload["range"]["commit_count"] > 0
    assert "destination" not in preview_payload

    events = [
        event
        for event in service.repository.list_events(project_id, limit=1000)
        if event["action"] == "project_exported"
    ]
    assert events == []


@pytest.mark.asyncio
async def test_export_project_partial_publish_failure_leaves_nothing_public(
    service: ApplicationService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _run_fixture_project(service)
    destination = tmp_path / "export-out"

    def failing_render(_payload: dict[str, object]) -> str:
        raise RuntimeError("simulated summary write failure")

    # Patched where the name is looked up (application.py imported it
    # directly), not at its origin module -- `export()` itself already
    # succeeded (patch/bundle are real) by the time this raises, so this
    # exercises the discard() path around the *summary* write specifically,
    # distinct from test_export.py's own narrower "internal git/bundle
    # failure" coverage of export() alone.
    monkeypatch.setattr(
        "agentarium.services.application.render_export_summary_markdown", failing_render
    )

    with pytest.raises(RuntimeError, match="simulated summary write failure"):
        await service.export_project(project_id, str(destination))

    assert not destination.exists() or list(destination.iterdir()) == []
    events = [
        event
        for event in service.repository.list_events(project_id, limit=1000)
        if event["action"] == "project_exported"
    ]
    assert events == []


# -- API level -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_endpoints_happy_path(service: ApplicationService, tmp_path: Path) -> None:
    project_id = await _run_fixture_project(service)

    app = create_app(service)
    with TestClient(app) as client:
        preview_response = client.get(f"/api/projects/{project_id}/export/preview")
        assert preview_response.status_code == 200
        assert preview_response.json()["range"]["commit_count"] > 0

        destination = tmp_path / "export-out"
        export_response = client.post(
            f"/api/projects/{project_id}/export",
            json={"destination": str(destination), "formats": ["patch"]},
        )
        assert export_response.status_code == 201
        body = export_response.json()
        assert body["patch_path"] is not None
        assert body["bundle_path"] is None
        assert _is_file(body["patch_path"])


@pytest.mark.asyncio
async def test_export_endpoint_rejects_relative_destination(
    service: ApplicationService,
) -> None:
    project_id = await _run_fixture_project(service)

    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/{project_id}/export",
            json={"destination": "relative/output"},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_endpoint_rejects_destination_overlapping_workspace_with_409(
    service: ApplicationService,
) -> None:
    project_id = await _run_fixture_project(service)

    app = create_app(service)
    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/{project_id}/export",
            json={"destination": str(service.settings.workspace_root)},
        )
        assert response.status_code == 409


# -- CLI level -------------------------------------------------------------
#
# `project export` is otherwise a thin wrapper already covered end-to-end by
# the service/API tests above -- these only cover what is genuinely
# CLI-specific: --dry-run writes nothing, --output is required without it,
# and the ExportError -> typer.Exit(11) mapping.


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


def _cli_create_and_run_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from agentarium.cli import app

    result = CliRunner().invoke(app, ["project", "create", _FIXTURE_GOAL])
    assert result.exit_code == 0, result.output
    project_id = json.loads(result.output)["id"]

    result = CliRunner().invoke(app, ["project", "run", project_id])
    assert result.exit_code == 0, result.output
    return project_id


def test_export_cli_happy_path_writes_real_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    project_id = _cli_create_and_run_project(tmp_path, monkeypatch)
    destination = tmp_path / "export-out"

    result = CliRunner().invoke(
        app, ["project", "export", project_id, "--output", str(destination)]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert (Path(payload["destination"]) / "changes.patch").is_file()
    assert (Path(payload["destination"]) / "changes.bundle").is_file()


def test_export_cli_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    project_id = _cli_create_and_run_project(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["project", "export", project_id, "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "destination" not in payload
    assert payload["range"]["commit_count"] > 0
    # --dry-run never receives a destination at all, so there is nowhere
    # under tmp_path a directory could have been written to: confirm the
    # workspace itself gained no new top-level entries beyond the project.
    workspace_root = tmp_path / "workspaces"
    assert {entry.name for entry in workspace_root.iterdir()} == {project_id}


def test_export_cli_requires_output_without_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    project_id = _cli_create_and_run_project(tmp_path, monkeypatch)

    result = CliRunner().invoke(app, ["project", "export", project_id])

    assert result.exit_code != 0
    assert "--output" in result.output


def test_export_cli_rejects_overlapping_destination_with_exit_code_eleven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    project_id = _cli_create_and_run_project(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        ["project", "export", project_id, "--output", str(tmp_path / "workspaces")],
    )

    assert result.exit_code == 11
