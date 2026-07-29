"""Task isolation contracts."""

from .base import ChangeSet, IsolationBackend, LocalWorkspaceIsolation
from .git_worktree import (
    GitChangeSet,
    GitWorktreeIsolation,
    IntegrationResult,
    IsolationError,
    WorktreeSession,
)

__all__ = [
    "ChangeSet",
    "GitChangeSet",
    "GitWorktreeIsolation",
    "IntegrationResult",
    "IsolationBackend",
    "IsolationError",
    "LocalWorkspaceIsolation",
    "WorktreeSession",
]
