from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChangeSet:
    task_id: str
    files: tuple[str, ...]
    diff: str


class IsolationBackend(ABC):
    @abstractmethod
    def prepare(self, project_id: str, task_id: str) -> Path:
        raise NotImplementedError

    @abstractmethod
    def collect(self, project_id: str, task_id: str) -> ChangeSet:
        raise NotImplementedError

    @abstractmethod
    def integrate(self, changes: ChangeSet) -> None:
        raise NotImplementedError


class LocalWorkspaceIsolation(IsolationBackend):
    """MVP adapter; keeps the API ready for branches and git worktrees."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self, project_id: str, task_id: str) -> Path:
        path = self.root / project_id / "tasks" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def collect(self, project_id: str, task_id: str) -> ChangeSet:
        path = self.root / project_id / "tasks" / task_id
        files = tuple(
            str(file.relative_to(self.root)) for file in path.rglob("*") if file.is_file()
        )
        return ChangeSet(task_id=task_id, files=files, diff="")

    def integrate(self, changes: ChangeSet) -> None:
        if changes.diff:
            raise NotImplementedError("Patch integration belongs to the worktree adapter")
