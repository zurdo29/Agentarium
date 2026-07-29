from pathlib import Path

import agentarium.execution.workspace as workspace_module
import pytest
from agentarium.config.settings import project_root
from agentarium.execution import (
    WorkArtifactProposal,
    WorkspaceFileProposal,
    WorkspaceMaterializer,
    WorkspaceRejected,
)
from pydantic import ValidationError


def _materializer(root: Path) -> WorkspaceMaterializer:
    return WorkspaceMaterializer(
        root,
        project_root() / "configs" / "policies" / "security.yaml",
    )


def test_workspace_materializer_stages_integrates_and_verifies_text(tmp_path: Path) -> None:
    materializer = _materializer(tmp_path / "workspaces")
    proposal = WorkspaceFileProposal(
        path="src/main.py",
        content="  print('preserved')\n",
        purpose="Entrada verificable",
    )

    evidence = materializer.materialize("project", "task", 1, [proposal])

    assert len(evidence) == 1
    assert Path(evidence[0].path).as_posix() == "workspaces/project/project/src/main.py"
    assert materializer.verify(evidence[0])
    integrated = tmp_path / evidence[0].path
    staged = (
        tmp_path
        / "workspaces"
        / "project"
        / "tasks"
        / "task"
        / "attempt-1"
        / "src"
        / "main.py"
    )
    assert integrated.read_text(encoding="utf-8") == "  print('preserved')\n"
    assert staged.read_text(encoding="utf-8") == "  print('preserved')\n"


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "/absolute/path.py",
        "C:\\outside\\path.py",
        "workspaces/project-id/index.html",
        ".git/config",
        ".env",
    ],
)
def test_workspace_contract_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceFileProposal(path=path, content="safe", purpose="test")


def test_workspace_contract_rejects_duplicate_normalized_paths() -> None:
    with pytest.raises(ValidationError, match="unique"):
        WorkArtifactProposal(
            artifact_type="implementation",
            title="Duplicate",
            summary="Duplicate paths",
            quality="verified",
            files=[
                {"path": "src/main.py", "content": "one", "purpose": "first"},
                {"path": "SRC/main.py", "content": "two", "purpose": "second"},
            ],
        )


def test_workspace_materializer_rejects_unsafe_project_identifier(tmp_path: Path) -> None:
    materializer = _materializer(tmp_path / "workspaces")
    proposal = WorkspaceFileProposal(
        path="README.md",
        content="safe",
        purpose="test",
    )

    with pytest.raises(WorkspaceRejected, match="single path components"):
        materializer.materialize("../outside", "task", 1, [proposal])


def test_workspace_materializer_recovers_when_windows_denies_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = _materializer(tmp_path / "workspaces")
    destination = tmp_path / "workspaces" / "project" / "project"
    destination.mkdir(parents=True)
    target = destination / "index.html"
    target.write_text("old", encoding="utf-8")
    proposal = WorkspaceFileProposal(
        path="index.html",
        content="<main>new</main>",
        purpose="Entrada web",
    )

    def deny_replace(_source: Path, _target: Path) -> None:
        raise PermissionError("simulated Windows file lock")

    monkeypatch.setattr(workspace_module.os, "replace", deny_replace)

    evidence = materializer.stage("project", destination, [proposal])

    assert target.read_text(encoding="utf-8") == "<main>new</main>"
    assert materializer.verify(evidence[0])


def test_workspace_contract_rejects_placeholder_implementation() -> None:
    with pytest.raises(ValidationError, match="not placeholders"):
        WorkspaceFileProposal(
            path="game.js",
            content="function update() { /* Lógica de movimiento aquí */ }",
            purpose="Juego funcional",
        )


def test_workspace_contract_allows_real_html_placeholder_attributes() -> None:
    proposal = WorkspaceFileProposal(
        path="index.html",
        content=(
            '<label for="search">Buscar</label>'
            '<input id="search" placeholder="Buscar por nombre">'
            "<style>input::placeholder { color: #64748b; }</style>"
        ),
        purpose="Formulario con ayuda de entrada",
    )

    assert 'placeholder="Buscar por nombre"' in proposal.content
