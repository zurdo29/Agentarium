from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from .contracts import WorkspaceFileProposal


class WorkspaceRejected(PermissionError):
    pass


@dataclass(frozen=True)
class WorkspaceFileEvidence:
    path: str
    checksum: str
    size_bytes: int
    purpose: str

    def as_check(self, *, verified: bool) -> dict[str, str | int | bool]:
        return {
            "check": "workspace_file_checksum",
            "path": self.path,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "purpose": self.purpose,
            "verified": verified,
        }


class WorkspaceMaterializer:
    """Stages text files per task and atomically integrates them into a project."""

    def __init__(self, workspace_root: Path, policy_path: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        workspace_policy = policy["workspace"]
        self.max_files = int(workspace_policy["max_files_per_artifact"])
        self.max_file_bytes = int(workspace_policy["max_file_bytes"])

    def materialize(
        self,
        project_id: str,
        work_item_id: str,
        attempt: int,
        files: list[WorkspaceFileProposal],
    ) -> list[WorkspaceFileEvidence]:
        """Compatibility path: stage files and immediately integrate them."""
        staging_root = self._contained_directory(
            project_id,
            "tasks",
            work_item_id,
            f"attempt-{attempt}",
        )
        self.stage(project_id, staging_root, files)
        project_root = self._contained_directory(project_id, "project")
        return self.stage(project_id, project_root, files)

    def stage(
        self,
        project_id: str,
        destination: Path,
        files: list[WorkspaceFileProposal],
    ) -> list[WorkspaceFileEvidence]:
        if not files:
            raise WorkspaceRejected("At least one workspace file is required")
        if len(files) > self.max_files:
            raise WorkspaceRejected(
                f"Workspace action exceeds the {self.max_files}-file limit"
            )

        project_scope = self._contained_directory(project_id)
        resolved_destination = destination.resolve()
        if not self._is_within(resolved_destination, project_scope):
            raise WorkspaceRejected("Workspace destination escaped the project scope")
        prepared: list[tuple[WorkspaceFileProposal, Path, bytes]] = []
        for proposal in files:
            content = proposal.content.encode("utf-8")
            if len(content) > self.max_file_bytes:
                raise WorkspaceRejected(
                    f"Workspace file exceeds the {self.max_file_bytes}-byte limit: "
                    f"{proposal.path}"
                )
            target = self._contained_file(resolved_destination, proposal.path)
            prepared.append((proposal, target, content))

        result: list[WorkspaceFileEvidence] = []
        for proposal, target, content in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                self._install_file(temporary, target, content)
            finally:
                temporary.unlink(missing_ok=True)
            result.append(
                self._evidence(proposal, target, content)
            )
        return result

    @staticmethod
    def _install_file(temporary: Path, target: Path, content: bytes) -> None:
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            # Windows can deny an atomic replacement while Git or an indexer holds
            # the existing worktree file open. A bounded in-place fallback keeps the
            # run recoverable; the checksum gate still verifies the exact bytes.
            if target.exists():
                try:
                    target.chmod(target.stat().st_mode | stat.S_IWRITE)
                except OSError:
                    pass
            try:
                with target.open("wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise WorkspaceRejected(
                    f"Workspace file could not be installed: {target.name}"
                ) from exc

    def project_evidence(
        self,
        project_id: str,
        files: list[WorkspaceFileProposal],
    ) -> list[WorkspaceFileEvidence]:
        project_root = self._contained_directory(project_id, "project")
        result: list[WorkspaceFileEvidence] = []
        for proposal in files:
            content = proposal.content.encode("utf-8")
            target = self._contained_file(project_root, proposal.path)
            evidence = self._evidence(proposal, target, content)
            if not self.verify(evidence):
                raise WorkspaceRejected(
                    f"Integrated workspace file failed verification: {proposal.path}"
                )
            result.append(evidence)
        return result

    def verify(self, evidence: WorkspaceFileEvidence) -> bool:
        path = (self.workspace_root.parent / evidence.path).resolve()
        if not self._is_within(path, self.workspace_root):
            return False
        return (
            path.is_file()
            and path.stat().st_size == evidence.size_bytes
            and hashlib.sha256(path.read_bytes()).hexdigest() == evidence.checksum
        )

    def _contained_directory(self, *parts: str) -> Path:
        if any(not part or Path(part).name != part for part in parts):
            raise WorkspaceRejected("Workspace identifiers must be single path components")
        candidate = self.workspace_root.joinpath(*parts).resolve()
        if not self._is_within(candidate, self.workspace_root):
            raise WorkspaceRejected("Workspace directory escaped the configured root")
        return candidate

    def _contained_file(self, root: Path, relative_path: str) -> Path:
        candidate = (root / Path(relative_path)).resolve()
        if not self._is_within(candidate, root):
            raise WorkspaceRejected("Workspace file escaped its authorized directory")
        current = candidate
        while current != root:
            if current.exists() and current.is_symlink():
                raise WorkspaceRejected("Workspace file crosses a symbolic link")
            current = current.parent
        return candidate

    def _evidence(
        self,
        proposal: WorkspaceFileProposal,
        target: Path,
        content: bytes,
    ) -> WorkspaceFileEvidence:
        return WorkspaceFileEvidence(
            path=str(target.relative_to(self.workspace_root.parent)),
            checksum=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            purpose=proposal.purpose,
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents
