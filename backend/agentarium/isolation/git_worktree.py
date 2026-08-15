from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from agentarium.execution import CommandResult, SafeCommandExecutor

_FILE_LOCK_POLL_SECONDS = 0.05
# Literal substring from git's own stderr (ADR 0022) -- deliberately not a
# regex, this exact wording is what git has produced every time so far.
_GIT_DIR_TOO_BIG_MARKER = "'$GIT_DIR' too big"


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
        # Keep this short: git's own internal worktree bookkeeping
        # (.git/worktrees/<name>/gitdir) hits "fatal: '$GIT_DIR' too big"
        # well before Windows' MAX_PATH, once the full task_id (a UUID) is
        # nested as its own directory on top of pytest's already-deep
        # tmp_path. Uniqueness comes from `token`, not from task_id being
        # spelled out in full — an 8-char prefix is only a human-readable
        # hint here.
        leaf = f"{task_id[:8]}-{attempt}-{token}"
        branch = f"agentarium/{leaf}"
        worktree = self._contained(project_id, "worktrees", leaf)
        async with self._project_lock(project_id):
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
        # Runs against session.path (the isolated per-attempt worktree), but
        # worktrees of the same repo share the object database and refs, so
        # this still needs the project-wide lock to avoid contending with a
        # concurrent prepare()/integrate()/discard() on a sibling worktree.
        async with self._project_lock(session.project_id):
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
        async with self._project_lock(changes.session.project_id):
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
        async with self._project_lock(session.project_id):
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
            message = f"{' '.join(command[:3])} failed: {detail}"
            if _GIT_DIR_TOO_BIG_MARKER in detail:
                # core.longpaths (already configured) only covers the
                # working-tree checkout, not git's own internal
                # .git/worktrees/<name>/gitdir bookkeeping (ADR 0022). The
                # per-attempt leaf is already shortened as much as
                # reasonable; the remaining variable is workspace_root
                # itself. Append, never replace -- the real git stderr
                # above stays intact for anyone grepping raw events.
                message += (
                    "\nEsto suele pasar cuando AGENTARIUM_WORKSPACE_ROOT "
                    "apunta a una ruta profunda en Windows -- ver "
                    "docs/guides/windows-setup.md. Probá una ruta corta, "
                    "por ejemplo C:\\agentarium-ws."
                )
            raise IsolationError(message)
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

    def _process_lock(self, project_id: str) -> asyncio.Lock:
        lock = self._locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[project_id] = lock
        return lock

    @asynccontextmanager
    async def _project_lock(self, project_id: str) -> AsyncIterator[None]:
        # Two layers: the asyncio.Lock serializes coroutines within this
        # process cheaply; the FileLock serializes across processes (e.g.
        # this worker vs. a CLI/API process touching the same repo), which
        # is the case an in-memory-only lock can never cover.
        #
        # Acquire/release run directly on the event loop thread via a
        # non-blocking poll, not asyncio.to_thread: filelock tracks lock
        # ownership per OS thread, and to_thread's worker-thread reuse is
        # non-deterministic, so an acquire and its matching release can land
        # on different threads and trip filelock's own (false-positive)
        # deadlock detector. A quick non-blocking attempt is cheap enough to
        # run inline.
        async with self._process_lock(project_id):
            project_dir = self.workspace_root / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            file_lock = FileLock(str(project_dir / ".isolation.lock"))
            while True:
                try:
                    file_lock.acquire(blocking=False)
                    break
                except Timeout:
                    await asyncio.sleep(_FILE_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                file_lock.release()

    def project_lock(self, project_id: str) -> AbstractAsyncContextManager[None]:
        """Public entry point to the same per-project lock
        prepare/collect/integrate/discard already share. P4.2's exporter
        reads/writes the same repo concurrently with those and needs the
        same serialization -- reusing this (asyncio.Lock + cross-process
        FileLock, with a real thread-affinity subtlety documented on
        `_project_lock` above) is deliberate, unlike the trivial
        two-line path formulas isolation submodules otherwise duplicate
        on purpose: a bug in a re-implementation could silently fail to
        provide mutual exclusion. No caller of this today ever already
        holds the lock itself, so there is no reentrancy/deadlock path."""
        return self._project_lock(project_id)
