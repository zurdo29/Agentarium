from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from agentarium.config.settings import project_root
from agentarium.domain.enums import WorkItemStatus
from agentarium.domain.models import Milestone, Project, WorkItem
from agentarium.execution import WorkspaceFileProposal, WorkspaceMaterializer
from agentarium.isolation import GitWorktreeIsolation
from agentarium.repositories import Database, Repository


def _policy_path() -> Path:
    return project_root() / "configs" / "policies" / "security.yaml"


@pytest.mark.asyncio
async def test_concurrent_prepare_collect_discard_cycles_do_not_corrupt_the_repo(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    project_id = str(uuid4())

    async def worker(cycles: int) -> None:
        for i in range(cycles):
            task_id = str(uuid4())
            session = await isolation.prepare(project_id, task_id, 1)
            proposal = WorkspaceFileProposal(
                path=f"file_{task_id[:8]}_{i}.txt",
                content="stress\n",
                purpose="stress test",
            )
            materializer.stage(project_id, session.path, [proposal])
            changes = await isolation.collect(session)
            assert changes.commit
            await isolation.discard(session)
            assert not session.path.exists()

    await asyncio.gather(*(worker(5) for _ in range(8)))

    project_root_dir = isolation._project_root(project_id)
    listing = await isolation._run(["git", "worktree", "list"], cwd=project_root_dir)
    remaining = [line for line in listing.stdout.splitlines() if line.strip()]
    assert len(remaining) == 1  # only the main worktree is left registered


@pytest.mark.asyncio
async def test_concurrent_status_reads_do_not_disrupt_transitions(
    tmp_path: Path,
) -> None:
    """A second Database/connection reading repeatedly must not corrupt or
    block the writer's transitions — the scenario that crashed the CLI when
    `project status` ran against a project a `project run` was still
    executing."""
    db_path = tmp_path / "agentarium.db"
    url = f"sqlite:///{db_path.as_posix()}"

    writer_db = Database(url)
    writer_db.create_all()
    writer_repo = Repository(writer_db)

    project = Project(title="Stress", goal="Stress test")
    writer_repo.create_project(project)
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    writer_repo.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea de estrés",
        description="Ciclo de transiciones repetido",
        expected_outputs=["resultado"],
        acceptance_criteria=["Existe"],
        status=WorkItemStatus.READY,
    )
    writer_repo.add_work_item(item)

    reader_db = Database(url)
    reader_repo = Repository(reader_db)
    stop = asyncio.Event()
    read_errors: list[Exception] = []

    async def reader() -> None:
        while not stop.is_set():
            try:
                reader_repo.get_work_item(item.id)
                reader_repo.list_work_items(project.id)
            except Exception as exc:  # pragma: no cover - assertion via list
                read_errors.append(exc)
            await asyncio.sleep(0)

    async def writer(cycles: int) -> None:
        cycle = [
            WorkItemStatus.ASSIGNED,
            WorkItemStatus.RUNNING,
            WorkItemStatus.READY,
        ]
        for i in range(cycles):
            target = cycle[i % len(cycle)]
            writer_repo.transition_work_item(item.id, target)

    reader_task = asyncio.create_task(reader())
    try:
        await writer(60)
    finally:
        stop.set()
        await reader_task

    assert not read_errors
    final = writer_repo.get_work_item(item.id)
    assert final.status is WorkItemStatus.READY
    assert final.version > 1

    writer_db.dispose()
    reader_db.dispose()
