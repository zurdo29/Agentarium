from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentarium.execution import CommandResult, SafeCommandExecutor


class IsolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorktreeSession:
    project_id: str
    task_id: str
    attempt: int
    branch: str
    path: Path


@dataclass(frozen=True)
class GitChangeSet:
    session: WorktreeSession
    commit: str
    files: tuple[str, ...]
    diff: str

    def as_evidence(self) -> dict[str, object]:
        return {
            "check": "isolated_change_set",
            "backend": "git_worktree",
            "branch": self.session.branch,
            "commit": self.commit,
            "files": list(self.files),
            "diff": self.diff,
            "verified": bool(self.commit),
        }


@dataclass(frozen=True)
class IntegrationResult:
    commit: str
    files: tuple[str, ...]


class GitWorktreeIsolation:
    """Creates one disposable Git worktree for each task attempt."""

    def __init__(self, workspace_root: Path, policy_path: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.executor = SafeCommandExecutor(self.workspace_root, policy_path)
        self._locks: dict[str, asyncio.Lock] = {}

    async def prepare(
        self,
        project_id: str,
        task_id: str,
        attempt: int,
    ) -> WorktreeSession:
        project_root = self._project_root(project_id)
        token = uuid4().hex[:10]
        branch = f"agentarium/{task_id}-attempt-{attempt}-{token}"
        worktree = self._contained(
            project_id,
            "worktrees",
            task_id,
            f"attempt-{attempt}-{token}",
        )
        async with self._lock(project_id):
            await self._ensure_repository(project_root)
            await asyncio.to_thread(
                worktree.parent.mkdir,
                parents=True,
                exist_ok=True,
            )
            await self._run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    branch,
                    str(worktree),
                    "main",
                ],
                cwd=project_root,
            )
        return WorktreeSession(
            project_id=project_id,
            task_id=task_id,
            attempt=attempt,
            branch=branch,
            path=worktree,
        )

    async def collect(self, session: WorktreeSession) -> GitChangeSet:
        await self._run(["git", "add", "-A"], cwd=session.path)
        diff_result = await self._run(
            ["git", "diff", "--cached", "--no-ext-diff", "--unified=3"],
            cwd=session.path,
        )
        files_result = await self._run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=session.path,
        )
        await self._run(
            [
                "git",
                "-c",
                "user.name=Agentarium",
                "-c",
                "user.email=agentarium@local",
                "commit",
                "--allow-empty",
                "-m",
                f"Task {session.task_id} attempt {session.attempt}",
            ],
            cwd=session.path,
        )
        commit = (
            await self._run(["git", "rev-parse", "HEAD"], cwd=session.path)
        ).stdout.strip()
        files = tuple(
            line.strip()
            for line in files_result.stdout.splitlines()
            if line.strip()
        )
        return GitChangeSet(
            session=session,
            commit=commit,
            files=files,
            diff=diff_result.stdout,
        )

    async def integrate(self, changes: GitChangeSet) -> IntegrationResult:
        project_root = self._project_root(changes.session.project_id)
        async with self._lock(changes.session.project_id):
            try:
                await self._run(
                    [
                        "git",
                        "-c",
                        "user.name=Agentarium",
                        "-c",
                        "user.email=agentarium@local",
                        "merge",
                        "--no-ff",
                        "--no-edit",
                        changes.session.branch,
                    ],
                    cwd=project_root,
                )
            except IsolationError:
                await self._run(
                    ["git", "merge", "--abort"],
                    cwd=project_root,
                    allow_failure=True,
                )
                raise
            commit = (
                await self._run(["git", "rev-parse", "HEAD"], cwd=project_root)
            ).stdout.strip()
        return IntegrationResult(commit=commit, files=changes.files)

    async def discard(self, session: WorktreeSession) -> None:
        project_root = self._project_root(session.project_id)
        async with self._lock(session.project_id):
            await self._run(
                ["git", "worktree", "remove", "--force", str(session.path)],
                cwd=project_root,
                allow_failure=True,
            )
            await self._run(
                ["git", "worktree", "prune"],
                cwd=project_root,
                allow_failure=True,
            )
            await self._run(
                ["git", "branch", "-D", session.branch],
                cwd=project_root,
                allow_failure=True,
            )

    async def _ensure_repository(self, project_root: Path) -> None:
        await asyncio.to_thread(project_root.mkdir, parents=True, exist_ok=True)
        git_exists = await asyncio.to_thread((project_root / ".git").is_dir)
        if git_exists:
            return
        await self._run(
            ["git", "init", "--initial-branch=main", "."],
            cwd=project_root,
        )
        await self._run(
            ["git", "config", "core.autocrlf", "false"],
            cwd=project_root,
        )
        await self._run(
            ["git", "config", "core.longpaths", "true"],
            cwd=project_root,
        )
        await self._run(["git", "add", "-A"], cwd=project_root)
        await self._run(
            [
                "git",
                "-c",
                "user.name=Agentarium",
                "-c",
                "user.email=agentarium@local",
                "commit",
                "--allow-empty",
                "-m",
                "Initialize Agentarium project",
            ],
            cwd=project_root,
        )

    async def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        allow_failure: bool = False,
    ) -> CommandResult:
        result = await self.executor.execute(command, cwd=cwd, timeout_seconds=30)
        if result.return_code != 0 and not allow_failure:
            detail = result.stderr.strip() or result.stdout.strip() or "Unknown Git error"
            raise IsolationError(f"{' '.join(command[:3])} failed: {detail}")
        return result

    def _project_root(self, project_id: str) -> Path:
        return self._contained(project_id, "project")

    def _contained(self, *parts: str) -> Path:
        if any(not part or Path(part).name != part for part in parts):
            raise IsolationError("Isolation identifiers must be single path components")
        path = self.workspace_root.joinpath(*parts).resolve()
        if path != self.workspace_root and self.workspace_root not in path.parents:
            raise IsolationError("Isolation path escaped the configured workspace")
        return path

    def _lock(self, project_id: str) -> asyncio.Lock:
        lock = self._locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[project_id] = lock
        return lock
