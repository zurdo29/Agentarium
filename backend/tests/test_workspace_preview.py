from __future__ import annotations

from pathlib import Path

from agentarium.api.app import create_app
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient


def test_preview_serves_html_and_assets_with_restrictive_headers(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un juego web local verificable")
    project_root = service.settings.workspace_root / project.id / "project"
    project_root.mkdir(parents=True)
    (project_root / "index.html").write_text(
        '<!doctype html><link rel="stylesheet" href="style.css"><h1>Juego listo</h1>',
        encoding="utf-8",
    )
    (project_root / "style.css").write_text("h1 { color: blue; }", encoding="utf-8")

    with TestClient(create_app(service)) as client:
        redirect = client.get(
            f"/api/projects/{project.id}/preview",
            follow_redirects=False,
        )
        assert redirect.status_code == 307
        assert redirect.headers["location"].endswith(f"/projects/{project.id}/preview/")

        preview = client.get(f"/api/projects/{project.id}/preview/")
        assert preview.status_code == 200
        assert "Juego listo" in preview.text
        assert preview.headers["cache-control"] == "no-store"
        assert "sandbox allow-scripts allow-same-origin" in preview.headers[
            "content-security-policy"
        ]
        assert "connect-src 'none'" in preview.headers["content-security-policy"]
        assert preview.headers["x-content-type-options"] == "nosniff"

        asset = client.get(f"/api/projects/{project.id}/preview/style.css")
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("text/css")


def test_preview_rejects_protected_and_unsupported_files(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un prototipo web seguro")
    project_root = service.settings.workspace_root / project.id / "project"
    protected = project_root / ".git"
    protected.mkdir(parents=True)
    (protected / "config").write_text("[core]", encoding="utf-8")
    (project_root / "notes.txt").write_text("internal notes", encoding="utf-8")

    with TestClient(create_app(service)) as client:
        protected_response = client.get(
            f"/api/projects/{project.id}/preview/.git/config"
        )
        unsupported_response = client.get(
            f"/api/projects/{project.id}/preview/notes.txt"
        )
        missing_entry = client.get(f"/api/projects/{project.id}/preview/")

    assert protected_response.status_code == 404
    assert unsupported_response.status_code == 404
    assert missing_entry.status_code == 404


def test_preview_resolver_does_not_escape_project(tmp_path: Path) -> None:
    from agentarium.execution import PreviewUnavailable, WorkspacePreview

    preview = WorkspacePreview(tmp_path / "workspaces")

    try:
        preview.resolve("../other", "index.html")
    except PreviewUnavailable:
        pass
    else:
        raise AssertionError("Preview accepted an invalid project identifier")
