"""Task isolation contracts."""

from .base import ChangeSet, IsolationBackend, LocalWorkspaceIsolation
from .git_worktree import (
    GitChangeSet,
    GitWorktreeIsolation,
    IntegrationResult,
    IsolationError,
    WorktreeSession,
)
from .import_source import ImportSource, ImportSourceError, SourceInspection

__all__ = [
    "ChangeSet",
    "GitChangeSet",
    "GitWorktreeIsolation",
    "ImportSource",
    "ImportSourceError",
    "IntegrationResult",
    "IsolationBackend",
    "IsolationError",
    "LocalWorkspaceIsolation",
    "SourceInspection",
    "WorktreeSession",
]
