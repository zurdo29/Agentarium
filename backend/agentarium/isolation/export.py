"""P4.2: reading a project's own already-accumulated git history
(`workspace_root/<project_id>/project`'s `main` branch, which
`GitWorktreeIsolation.integrate()` already grows by one real merge
commit per accepted work item) and producing a patch and/or bundle a
user can apply to their own copy of the original -- never the other
way around. The original (`imported_source_path`) is never read or
written by this module; its persisted value participates only as a
path comparison, to reject an export destination that lands inside it.

Deliberately separate from `GitWorktreeIsolation` for the same reason
`import_source.py` already is: this module's job -- read the project's
own history once, write a snapshot of it somewhere outside
`workspace_root` -- is a different lifecycle. `SafeCommandExecutor` is
not reused either: its `cwd`-inside-`workspace_root` check is correct
for `GitWorktreeIsolation`'s own commands, but export's whole point is
to write outside `workspace_root`; every git command here is read-only
against the project's own repo (`cwd` stays inside `workspace_root`)
with a fixed, hardcoded subcommand list, never content controlled by
an LLM or operator, so there is nothing `SafeCommandExecutor`'s
sandboxing would add. The one thing this module does reuse from
`GitWorktreeIsolation` is its per-project lock (`project_lock()`) --
real, subtle coordination logic, unlike the trivial path formulas
duplicated below.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .git_worktree import GitWorktreeIsolation

_GIT_READ_TIMEOUT_SECONDS = 30
_BUNDLE_TIMEOUT_SECONDS = 120
_COMMIT_LOG_SEPARATOR = "\x1f"
_SUPPORTED_FORMATS = frozenset({"patch", "bundle"})


class ExportError(RuntimeError):
    """A project's changes cannot be exported -- always a real, expected
    rejection (no repository yet, nothing to export since the base
    commit, destination overlaps the workspace or the original,
    git/database records not yet aligned), never a stand-in for an
    infrastructure failure."""


@dataclass(frozen=True)
class ExportedCommit:
    commit: str
    author: str
    authored_at: str
    subject: str


@dataclass(frozen=True)
class ExportManifest:
    project_id: str
    base_commit: str
    base_is_import_commit: bool
    head_commit: str
    commit_count: int
    commits: tuple[ExportedCommit, ...]
    files_changed: tuple[str, ...]


@dataclass(frozen=True)
class PendingExport:
    """Everything `export()` already wrote, staged under a hidden name
    that is not yet the export's public identity. `publish()` renames
    `staging_dir` to `final_dir`; `discard()` removes `staging_dir`
    outright. The caller (`ApplicationService`) is expected to write
    its own additional files (the summary) into `staging_dir` before
    calling either."""

    manifest: ExportManifest
    staging_dir: Path
    final_dir: Path
    patch_path: Path | None
    bundle_path: Path | None


@dataclass(frozen=True)
class ExportResult:
    manifest: ExportManifest
    destination: Path
    patch_path: Path | None
    bundle_path: Path | None


def _force_rmtree(path: Path) -> None:
    """`shutil.rmtree(path, ignore_errors=True)` silently leaves files
    behind on Windows if any are read-only -- the same problem
    `import_source.py` already found and fixed for git's own read-only
    pack files. Duplicated here rather than imported (isolation
    submodules stay decoupled from each other): clears the read-only
    bit and retries before giving up on any single entry."""

    def _on_error(func, target_path, _exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target_path, stat.S_IWRITE)
            func(target_path)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


class ProjectExporter:
    """Reads `workspace_root/<project_id>/project`'s own git history
    exactly as far as needed to produce a patch and/or bundle somewhere
    outside `workspace_root`. Never touches `Repository`/SQLite itself
    -- callers pass in whatever database-derived facts a check needs
    (e.g. `last_known_integration_commit`) as plain parameters."""

    def __init__(self, workspace_root: Path, isolation: GitWorktreeIsolation) -> None:
        self.workspace_root = workspace_root.resolve()
        self._isolation = isolation

    async def preview(self, project_id: str, *, base_commit: str | None) -> ExportManifest:
        """Read-only, writes nothing, never validates git/DB alignment
        -- a preview that is momentarily stale has no consequence since
        nothing gets published from it."""
        project_root = self._project_root(project_id)
        async with self._isolation.project_lock(project_id):
            base, base_is_import = await self._resolve_base(project_root, base_commit)
            return await self._build_manifest(project_root, project_id, base, base_is_import)

    async def export(
        self,
        project_id: str,
        destination: Path,
        *,
        base_commit: str | None,
        imported_source_path: str | None,
        last_known_integration_commit: str | None,
        has_any_integration_event: bool,
        formats: frozenset[str] = _SUPPORTED_FORMATS,
    ) -> PendingExport:
        unknown = formats - _SUPPORTED_FORMATS
        if unknown:
            raise ExportError(f"Formato desconocido: {sorted(unknown)}")
        if not formats:
            raise ExportError("Debe solicitarse al menos un formato: patch y/o bundle")
        if not destination.is_absolute():
            raise ExportError("destination debe ser una ruta absoluta")
        resolved_destination = await asyncio.to_thread(destination.resolve)
        if self._overlaps(resolved_destination, self.workspace_root):
            raise ExportError("El destino se solapa con el workspace de Agentarium")
        if imported_source_path is not None and self._overlaps(
            resolved_destination, Path(imported_source_path)
        ):
            raise ExportError(
                "El destino se solapa con el repositorio original importado; "
                "elegí una carpeta distinta."
            )

        project_root = self._project_root(project_id)
        base_name = self._export_dirname(project_id)
        final_dir = resolved_destination / base_name
        staging_dir = resolved_destination / f".{base_name}.staging"

        async with self._isolation.project_lock(project_id):
            base, base_is_import = await self._resolve_base(project_root, base_commit)
            manifest = await self._build_manifest(project_root, project_id, base, base_is_import)
            self._validate_alignment(
                manifest, last_known_integration_commit, has_any_integration_event
            )
            patch_path: Path | None = None
            bundle_path: Path | None = None
            try:
                await asyncio.to_thread(staging_dir.mkdir, parents=True, exist_ok=False)
                if "patch" in formats:
                    patch_path = staging_dir / "changes.patch"
                    patch_bytes = await self._run_git_bytes_checked(
                        ["format-patch", "--binary", f"{base}..main", "--stdout"],
                        cwd=project_root,
                    )
                    await asyncio.to_thread(patch_path.write_bytes, patch_bytes)
                if "bundle" in formats:
                    bundle_path = staging_dir / "changes.bundle"
                    await self._run_git_checked(
                        ["bundle", "create", str(bundle_path), f"{base}..main"],
                        cwd=project_root,
                        timeout_seconds=_BUNDLE_TIMEOUT_SECONDS,
                    )
            except Exception:
                await asyncio.to_thread(_force_rmtree, staging_dir)
                raise

        return PendingExport(
            manifest=manifest,
            staging_dir=staging_dir,
            final_dir=final_dir,
            patch_path=patch_path,
            bundle_path=bundle_path,
        )

    async def publish(self, pending: PendingExport) -> ExportResult:
        """Atomically renames the staging directory to its final public
        name. Call only once every file that belongs in the export
        (including the caller's own summary.json/summary.md) has
        already been written into `pending.staging_dir`."""
        await asyncio.to_thread(pending.staging_dir.rename, pending.final_dir)
        return ExportResult(
            manifest=pending.manifest,
            destination=pending.final_dir,
            patch_path=(
                pending.final_dir / pending.patch_path.name if pending.patch_path else None
            ),
            bundle_path=(
                pending.final_dir / pending.bundle_path.name if pending.bundle_path else None
            ),
        )

    async def discard(self, pending: PendingExport) -> None:
        """Cleans up a staging directory that will never be published --
        `export()`'s own git/bundle work already succeeded, but
        something after that (typically the caller writing its own
        summary files) failed."""
        await asyncio.to_thread(_force_rmtree, pending.staging_dir)

    # -- internal: base commit / manifest / alignment ----------------------------

    async def _resolve_base(
        self, project_root: Path, base_commit: str | None
    ) -> tuple[str, bool]:
        if not await asyncio.to_thread(project_root.is_dir):
            raise ExportError(
                "El proyecto todavía no tiene un repositorio propio -- "
                "no se ejecutó ningún work item todavía."
            )
        if base_commit is not None:
            returncode, _, _ = await self._run_git(
                ["merge-base", "--is-ancestor", base_commit, "main"], cwd=project_root
            )
            if returncode != 0:
                raise ExportError(
                    f"El commit importado {base_commit[:12]} ya no es ancestro "
                    "de 'main'; no se puede calcular un rango de exportación "
                    "consistente."
                )
            return base_commit, True
        roots = (
            await self._run_git_checked(
                ["rev-list", "--max-parents=0", "main"], cwd=project_root
            )
        ).strip().splitlines()
        if len(roots) != 1:
            raise ExportError(
                f"No se encontró un único commit raíz ({len(roots)} candidatos)."
            )
        return roots[0], False

    async def _build_manifest(
        self, project_root: Path, project_id: str, base: str, base_is_import: bool
    ) -> ExportManifest:
        head_commit = (
            await self._run_git_checked(["rev-parse", "main"], cwd=project_root)
        ).strip()
        range_spec = f"{base}..main"
        log_output = await self._run_git_checked(
            [
                "log",
                f"--format=%H{_COMMIT_LOG_SEPARATOR}%aN{_COMMIT_LOG_SEPARATOR}"
                f"%aI{_COMMIT_LOG_SEPARATOR}%s",
                range_spec,
            ],
            cwd=project_root,
        )
        commits = tuple(
            self._parse_commit_line(line)
            for line in log_output.splitlines()
            if line.strip()
        )
        if not commits:
            raise ExportError(
                "No hay cambios para exportar: 'main' está en el mismo commit "
                "que la base de exportación."
            )
        files_output = await self._run_git_checked(
            ["diff", "--name-only", range_spec], cwd=project_root
        )
        files_changed = tuple(
            sorted({line.strip() for line in files_output.splitlines() if line.strip()})
        )
        return ExportManifest(
            project_id=project_id,
            base_commit=base,
            base_is_import_commit=base_is_import,
            head_commit=head_commit,
            commit_count=len(commits),
            commits=commits,
            files_changed=files_changed,
        )

    @staticmethod
    def _parse_commit_line(line: str) -> ExportedCommit:
        commit, author, authored_at, subject = line.split(_COMMIT_LOG_SEPARATOR, 3)
        return ExportedCommit(
            commit=commit, author=author, authored_at=authored_at, subject=subject
        )

    def _validate_alignment(
        self,
        manifest: ExportManifest,
        last_known_integration_commit: str | None,
        has_any_integration_event: bool,
    ) -> None:
        # `_build_manifest` already rejected an empty range (HEAD == base)
        # before this ever runs, so reaching here always means at least
        # one commit is about to be exported -- there must already be a
        # matching, recorded integration event for the current HEAD.
        # `integrate()` (git_worktree.py) releases the project lock as
        # soon as its own merge finishes, *before* returning to
        # engine.py, which only then writes the `change_set_integrated`
        # event -- so a real, if narrow, window exists where `main` has
        # already moved but the database does not know it yet. This
        # check can only ever fail toward the safe side: `HEAD` is read
        # fresh under the lock (so it cannot move again during this
        # check), while `last_known_integration_commit` was necessarily
        # read *before* the lock was acquired -- the only way they can
        # disagree is git having moved past what the database already
        # knew, never the other way around.
        if not has_any_integration_event or last_known_integration_commit != manifest.head_commit:
            raise ExportError(
                "El historial de git y los registros de integración todavía "
                "no están alineados (una integración puede estar terminando "
                "de registrarse); probá exportar de nuevo en un momento."
            )

    # -- internal: shared plumbing --------------------------------------------

    def _export_dirname(self, project_id: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"agentarium-export-{project_id[:8]}-{stamp}-{uuid4().hex[:8]}"

    @staticmethod
    def _overlaps(a: Path, b: Path) -> bool:
        if a == b:
            return True
        if b in a.parents:
            return True
        return a in b.parents

    def _project_root(self, project_id: str) -> Path:
        # Duplicates GitWorktreeIsolation._project_root / ImportSource's
        # own copy of the same formula, on purpose -- same reasoning
        # import_source.py already documents for keeping isolation
        # submodules decoupled from each other over two lines of path
        # math.
        return self.workspace_root / project_id / "project"

    async def _spawn_git(
        self, args: list[str], *, cwd: Path, timeout_seconds: int
    ) -> tuple[int, bytes, bytes]:
        command = ["git", "--no-optional-locks", *args]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ExportError(
                f"El comando 'git {args[0] if args else ''}' tardó demasiado"
            ) from None
        return (
            process.returncode if process.returncode is not None else -1,
            stdout_bytes,
            stderr_bytes,
        )

    async def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = _GIT_READ_TIMEOUT_SECONDS,
    ) -> tuple[int, str, str]:
        returncode, stdout_bytes, stderr_bytes = await self._spawn_git(
            args, cwd=cwd, timeout_seconds=timeout_seconds
        )
        return (
            returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def _run_git_checked(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = _GIT_READ_TIMEOUT_SECONDS,
    ) -> str:
        returncode, stdout, stderr = await self._run_git(
            args, cwd=cwd, timeout_seconds=timeout_seconds
        )
        if returncode != 0:
            raise ExportError(
                f"git {' '.join(args)} falló ({returncode}): "
                f"{stderr.strip() or stdout.strip() or 'sin salida'}"
            )
        return stdout

    async def _run_git_bytes_checked(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = _GIT_READ_TIMEOUT_SECONDS,
    ) -> bytes:
        """Like `_run_git_checked`, but for a call whose stdout must
        survive byte-for-byte -- `format-patch --binary`'s output can
        contain base85-armored binary hunks that must never round-trip
        through a decode/re-encode cycle. `_run_git`'s shared path
        decodes stdout with `errors="replace"`, which is fine for git's
        own diagnostic text but would silently corrupt patch content
        here, producing a `.patch` file `git am` either rejects outright
        or, worse, applies with silently wrong binary content. `git
        bundle create` needs no equivalent: it writes its output file
        directly to the path given as an argument, never through
        Python's stdout capture at all."""
        returncode, stdout_bytes, stderr_bytes = await self._spawn_git(
            args, cwd=cwd, timeout_seconds=timeout_seconds
        )
        if returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            raise ExportError(
                f"git {' '.join(args)} falló ({returncode}): "
                f"{stderr_text.strip() or 'sin salida'}"
            )
        return stdout_bytes
