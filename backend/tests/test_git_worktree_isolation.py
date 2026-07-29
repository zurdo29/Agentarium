from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.execution import WorkspaceFileProposal, WorkspaceMaterializer
from agentarium.isolation import GitWorktreeIsolation


def _policy_path() -> Path:
    return project_root() / "configs" / "policies" / "security.yaml"


@pytest.mark.asyncio
async def test_approved_worktree_is_integrated_and_cleaned(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    session = await isolation.prepare("project", "task", 1)
    proposal = WorkspaceFileProposal(
        path="README.md",
        content="# Integrated\n",
        purpose="Integration fixture",
    )

    materializer.stage("project", session.path, [proposal])
    changes = await isolation.collect(session)

    product_file = workspace_root / "project" / "project" / "README.md"
    assert not product_file.exists()
    assert changes.files == ("README.md",)
    assert "README.md" in changes.diff
    assert changes.commit

    integration = await isolation.integrate(changes)
    await isolation.discard(session)

    assert integration.commit
    assert product_file.read_text(encoding="utf-8") == "# Integrated\n"
    assert not session.path.exists()
    evidence = materializer.project_evidence("project", [proposal])
    assert materializer.verify(evidence[0])


@pytest.mark.asyncio
async def test_rejected_worktree_never_reaches_main(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    session = await isolation.prepare("project", "rejected-task", 1)
    proposal = WorkspaceFileProposal(
        path="src/rejected.py",
        content="raise RuntimeError('candidate only')\n",
        purpose="Rejected fixture",
    )

    materializer.stage("project", session.path, [proposal])
    changes = await isolation.collect(session)
    await isolation.discard(session)

    assert changes.commit
    assert not session.path.exists()
    assert not (
        workspace_root / "project" / "project" / "src" / "rejected.py"
    ).exists()
