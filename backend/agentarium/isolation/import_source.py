"""P4.1: safely reading an existing external folder/repo and copying it,
once, into a location `GitWorktreeIsolation` already knows how to manage.

The one invariant everything here exists to guarantee: the external
`source_path` is only ever *read* -- during inspection and during the
import process itself (which includes re-reading it immediately after
the copy, to detect a source that changed mid-import) -- and is never
written to. Once `import_into()` returns, this module never touches the
source again; only the copy under `workspace_root` is used from then on.

Deliberately separate from `GitWorktreeIsolation` (`git_worktree.py`):
that class manages worktrees Agentarium already owns, and its own
`_contained()` path check cannot even accept a multi-segment external
path as an argument. This module's job -- read something untrusted once,
copy it -- is a different lifecycle, so it does not implement it either.
`SafeCommandExecutor` is not reused here either: its `cwd`-inside-
`workspace_root` check would reject every command this module needs to
run against the source by design, and reusing it would be a category
error, not a relaxation of it. Every git invocation here uses its own
fixed, hardcoded subcommand list -- never content controlled by an LLM
or an operator -- and runs async so a slow import never blocks the
FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_INSPECT_TIMEOUT_SECONDS = 30
_CLONE_TIMEOUT_SECONDS = 300


class ImportSourceError(RuntimeError):
    """A source folder/repo cannot be imported -- always a real, expected
    rejection (not found, not a directory, dirty, has submodules, overlaps
    the workspace, changed mid-import), never a stand-in for an
    infrastructure failure."""


@dataclass(frozen=True)
class SourceInspection:
    eligible: bool
    reason: str | None
    exists: bool
    is_directory: bool
    is_git_repo: bool
    head_commit: str | None
    branch: str | None
    detached_head: bool
    is_dirty: bool
    has_submodules: bool
    file_count_estimate: int | None
    size_bytes_estimate: int | None


def _force_rmtree(path: Path) -> None:
    """`shutil.rmtree(path, ignore_errors=True)` silently leaves files
    behind on Windows if any are read-only -- confirmed empirically: a
    real `git clone`'s pack files (`.git/objects/pack/*.pack`/`*.idx`)
    are created read-only, and plain `ignore_errors=True` skips them
    rather than clearing the attribute, leaving the "cleaned up"
    directory only partially removed. This clears the read-only bit and
    retries before giving up on any single entry."""

    def _on_error(func, target_path, _exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target_path, stat.S_IWRITE)
            func(target_path)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def _is_reparse_point(path: Path) -> bool:
    """True for a symlink, junction, or other Windows reparse point.

    `Path.is_symlink()` alone is not reliable for Windows junctions on
    every Python/OS combination, so this also checks the raw
    FILE_ATTRIBUTE_REPARSE_POINT bit via `os.lstat` (never `os.stat`,
    which would follow the link/junction we are specifically trying to
    detect without following).
    """
    if path.is_symlink():
        return True
    if sys.platform != "win32":
        return False
    try:
        attributes = os.lstat(path).st_file_attributes
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class ImportSource:
    """Reads an external folder/repo exactly as far as needed to copy it
    once into `workspace_root/<project_id>/project` -- the same location
    `GitWorktreeIsolation._project_root` already uses for a greenfield
    project. Duplicated here as two lines rather than imported, on
    purpose: keeps `git_worktree.py` at zero changes for this phase."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    async def inspect(self, source_path: Path) -> SourceInspection:
        """Read-only. Never raises for an expected outcome -- missing,
        not a directory, dirty, has submodules, overlaps the workspace
        are all valid results with `eligible=False`, not errors."""
        if not source_path.is_absolute():
            return self._ineligible(reason="source_path debe ser una ruta absoluta")
        resolved = await asyncio.to_thread(source_path.resolve)
        if self._overlaps_workspace(resolved):
            return self._ineligible(
                reason="source_path se solapa con el workspace de Agentarium"
            )
        exists = await asyncio.to_thread(resolved.exists)
        if not exists:
            return self._ineligible(reason="La ruta no existe", exists=False)
        is_directory = await asyncio.to_thread(resolved.is_dir)
        if not is_directory:
            return self._ineligible(
                reason="La ruta no es un directorio", exists=True, is_directory=False
            )

        is_git_repo = await self._is_git_repo(resolved)
        head_commit: str | None = None
        branch: str | None = None
        detached = False
        is_dirty = False
        has_submodules = False
        reparse_point: Path | None = None
        if is_git_repo:
            head_commit, is_dirty = await self._capture_snapshot(resolved)
            if head_commit is not None:
                has_submodules = await self._has_submodules(resolved)
                branch, detached = await self._branch_info(resolved)
        else:
            # Mirrors _import_flat's own rejection: a symlink/junction the
            # UI never flagged as ineligible would let a user click
            # "importar" believing it will succeed and only find out from
            # import_into()'s rejection -- inspect() must already know.
            reparse_point = await asyncio.to_thread(self._find_reparse_point, resolved)

        file_count, size_bytes = await asyncio.to_thread(self._estimate_size, resolved)

        reason: str | None = None
        eligible = True
        if is_git_repo and is_dirty:
            eligible = False
            reason = (
                "El repositorio tiene cambios sin commitear. Commiteá o "
                "guardá con stash antes de importar."
            )
        elif is_git_repo and has_submodules:
            eligible = False
            reason = "El repositorio tiene submódulos; no soportado en esta fase."
        elif reparse_point is not None:
            eligible = False
            reason = (
                "La carpeta contiene un symlink/junction/reparse point no "
                f"soportado: {reparse_point}"
            )

        return SourceInspection(
            eligible=eligible,
            reason=reason,
            exists=True,
            is_directory=True,
            is_git_repo=is_git_repo,
            head_commit=head_commit,
            branch=branch,
            detached_head=detached,
            is_dirty=is_dirty,
            has_submodules=has_submodules,
            file_count_estimate=file_count,
            size_bytes_estimate=size_bytes,
        )

    async def import_into(self, source_path: Path, project_id: str) -> SourceInspection:
        """Runs once, at project-creation time. Never trusts a prior
        `inspect()` call -- repeats every check itself, including a
        second read of the source immediately after the copy finishes,
        to detect a source that changed while the copy was running."""
        if not source_path.is_absolute():
            raise ImportSourceError("source_path debe ser una ruta absoluta")
        resolved = await asyncio.to_thread(source_path.resolve)
        if self._overlaps_workspace(resolved):
            raise ImportSourceError(
                "source_path se solapa con el workspace de Agentarium"
            )
        exists = await asyncio.to_thread(resolved.exists)
        is_directory = exists and await asyncio.to_thread(resolved.is_dir)
        if not exists or not is_directory:
            raise ImportSourceError("source_path no existe o no es un directorio")

        destination = self._project_root(project_id)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            is_git_repo = await self._is_git_repo(resolved)
            if is_git_repo:
                head_commit, is_dirty = await self._capture_snapshot(resolved)
                if is_dirty:
                    raise ImportSourceError(
                        "El repositorio tiene cambios sin commitear. Commiteá o "
                        "guardá con stash antes de importar."
                    )
                if head_commit is not None:
                    if await self._has_submodules(resolved):
                        raise ImportSourceError(
                            "El repositorio tiene submódulos; no soportado en "
                            "esta fase."
                        )
                    return await self._import_git(
                        resolved, destination, expected_head=head_commit
                    )
            # Not a git repo, or a git repo with no commits yet (unborn
            # HEAD): nothing to clone, fall through to the plain-folder
            # path, which handles an empty/no-.git source uniformly.
            return await self._import_flat(resolved, destination)
        except Exception:
            # destination.parent (workspace_root/<project_id>/) is created
            # above and, until this call succeeds, is owned entirely by
            # this one import attempt (project_id is a fresh id) -- clean
            # up the whole thing, not just `destination` itself, so a
            # failure before any content ever gets created (e.g. the
            # dirty-source rejection, which fails before cloning/copying
            # anything) does not leave an empty directory behind.
            await asyncio.to_thread(_force_rmtree, destination.parent)
            raise

    # -- internal: git-repo path ------------------------------------------------

    async def _import_git(
        self, source: Path, destination: Path, *, expected_head: str
    ) -> SourceInspection:
        # --no-local: never hardlinks between original and copy, even on
        # the same volume -- this is deliberate, not a missed
        # optimization. It routes clone through the same object-transfer
        # code path a remote clone would use, sidestepping the whole
        # class of "what if something rewrites a shared object" concern
        # without depending on git version/CVE history for safety.
        #
        # --no-checkout, then config, then an explicit checkout: verified
        # by hand that `git -c core.autocrlf=false clone ...` does NOT
        # reliably persist that override into the new repo's config (it
        # applies only to the clone invocation itself, and the checkout
        # clone performs internally already happened by the time it would
        # matter) -- confirmed empirically, not assumed, the same mistake
        # this comment used to make. Cloning without checking out first,
        # setting config against the now-existing (but still empty)
        # destination repo, and only then materializing the working tree
        # is the sequence that actually applies core.autocrlf before any
        # working-tree file is written -- same reasoning as
        # `_ensure_repository()` below, just split across clone's two
        # phases instead of `init` never writing anything until told to.
        #
        # core.longpaths is different: unlike core.autocrlf (which only
        # matters for working-tree files, and --no-checkout defers all of
        # those), clone's own object-transfer phase can itself write
        # long-named pack/idx/keep files under a deep workspace_root --
        # found by hand on this machine (a 266-character destination path
        # failed with git's own "Filename too long" before any config
        # call had a chance to run against the not-yet-existing
        # destination). `-c` on the clone invocation itself is the only
        # way to cover that phase; the persisted `git config` call right
        # after still matters too, for every later command against this
        # destination (checkout, commit, ...).
        await self._run_git_checked(
            [
                "-c",
                "core.longpaths=true",
                "clone",
                "--no-local",
                "--no-checkout",
                "--",
                str(source),
                str(destination),
            ],
            cwd=self.workspace_root,
            timeout_seconds=_CLONE_TIMEOUT_SECONDS,
        )
        await self._run_git_checked(["config", "core.autocrlf", "false"], cwd=destination)
        await self._run_git_checked(["config", "core.longpaths", "true"], cwd=destination)
        # `HEAD` already points at whatever the source had checked out
        # (branch or detached commit) -- `-f` populates the working tree
        # from it without needing to know in advance which case this is.
        await self._run_git_checked(["checkout", "-f", "HEAD"], cwd=destination)

        # `checkout -f` exiting 0 is not itself proof the destination
        # landed on the right commit -- verified directly, not assumed,
        # the same discipline as everything else in this method. A
        # mismatch here (a corrupted clone, an unexpected default-branch
        # resolution, ...) must reject and clean up rather than persist
        # a `head_commit` that does not actually match what was cloned.
        destination_head = (
            await self._run_git_checked(["rev-parse", "HEAD"], cwd=destination)
        ).strip()
        if destination_head != expected_head:
            raise ImportSourceError(
                "El commit resultante en el destino no coincide con el "
                "commit esperado del origen; la importación se rechazó."
            )

        # TOCTOU close: re-read the source now that the copy is done. A
        # single check before cloning cannot catch a change that happens
        # *during* the clone -- only a before-and-after comparison can
        # detect that and refuse to accept an inconsistent result.
        post_head, post_dirty = await self._capture_snapshot(source)
        if post_head != expected_head or post_dirty:
            raise ImportSourceError(
                "El origen cambió mientras se importaba; la importación se "
                "rechazó."
            )

        # Closes a standing (if unused today) path back to the original --
        # nothing in this codebase calls fetch/pull/push, but leaving
        # `origin` configured would be authority nobody uses left lying
        # around, not authority actually denied.
        await self._run_git_checked(["remote", "remove", "origin"], cwd=destination)

        # Unlike every other call in this method, a non-zero exit here is
        # itself the answer (detached HEAD), not a failure -- `_run_git`
        # stays unchecked for this one on purpose.
        returncode, stdout, _ = await self._run_git(
            ["symbolic-ref", "-q", "HEAD"], cwd=destination
        )
        if returncode != 0:
            # Detached HEAD in the source: `branch -m` refuses outright
            # on a detached HEAD, so this needs a different remedy.
            await self._run_git_checked(["checkout", "-b", "main"], cwd=destination)
        else:
            current_branch = stdout.strip().removeprefix("refs/heads/")
            if current_branch != "main":
                await self._run_git_checked(
                    ["branch", "-m", current_branch, "main"], cwd=destination
                )

        return SourceInspection(
            eligible=True,
            reason=None,
            exists=True,
            is_directory=True,
            is_git_repo=True,
            head_commit=expected_head,
            branch="main",
            detached_head=False,
            is_dirty=False,
            has_submodules=False,
            file_count_estimate=None,
            size_bytes_estimate=None,
        )

    # -- internal: plain-folder path ---------------------------------------------

    async def _import_flat(self, source: Path, destination: Path) -> SourceInspection:
        """No git history to preserve/verify here, and -- unlike the git
        path above -- no snapshot concept to check before and after
        either. A concurrent modification of a plain folder during this
        copy has no detection mechanism in this phase; this is a known,
        documented limitation, not something this method silently papers
        over."""
        # Scan-then-copy, not check-as-you-go: a rejection must happen
        # before any destination content exists, not mid-copy.
        await asyncio.to_thread(self._reject_reparse_points, source)
        await asyncio.to_thread(self._copy_tree, source, destination)

        await self._run_git_checked(
            ["init", "--initial-branch=main", "."], cwd=destination
        )
        await self._run_git_checked(
            ["config", "core.autocrlf", "false"], cwd=destination
        )
        await self._run_git_checked(
            ["config", "core.longpaths", "true"], cwd=destination
        )
        await self._run_git_checked(["add", "-A"], cwd=destination)
        await self._run_git_checked(
            [
                "-c",
                "user.name=Agentarium",
                "-c",
                "user.email=agentarium@local",
                "commit",
                "--allow-empty",
                "-m",
                "Import external project",
            ],
            cwd=destination,
        )
        head_commit = (
            await self._run_git_checked(["rev-parse", "HEAD"], cwd=destination)
        ).strip()

        return SourceInspection(
            eligible=True,
            reason=None,
            exists=True,
            is_directory=True,
            is_git_repo=False,
            head_commit=head_commit,
            branch="main",
            detached_head=False,
            is_dirty=False,
            has_submodules=False,
            file_count_estimate=None,
            size_bytes_estimate=None,
        )

    def _find_reparse_point(self, source: Path) -> Path | None:
        for root, dirs, files in os.walk(source, followlinks=False):
            root_path = Path(root)
            relative_parts = root_path.relative_to(source).parts
            if relative_parts and relative_parts[0] == ".git":
                dirs[:] = []
                continue
            for name in (*dirs, *files):
                entry = root_path / name
                if _is_reparse_point(entry):
                    return entry
        return None

    def _reject_reparse_points(self, source: Path) -> None:
        found = self._find_reparse_point(source)
        if found is not None:
            raise ImportSourceError(
                "La carpeta contiene un symlink/junction/reparse "
                f"point no soportado: {found}"
            )

    def _copy_tree(self, source: Path, destination: Path) -> None:
        # Never shutil.copytree(): it follows symlinks/junctions outward
        # by default with no cycle detection, and does not abort on the
        # first locked/unreadable file -- it aggregates errors and can
        # leave a complete-looking but silently incomplete tree. This
        # walks explicitly, always excludes a top-level `.git` (also
        # handles the "git repo with zero commits" case cleanly, with no
        # special-casing), and aborts on the first failure -- the caller
        # (`import_into`) removes the whole partial destination on any
        # exception here.
        for root, dirs, files in os.walk(source, followlinks=False):
            root_path = Path(root)
            relative = root_path.relative_to(source)
            if relative.parts and relative.parts[0] == ".git":
                dirs[:] = []
                continue
            target_dir = destination / relative
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = root_path / name
                try:
                    shutil.copy2(source_file, target_dir / name)
                except OSError as exc:
                    raise ImportSourceError(
                        f"No se pudo copiar {source_file}: {exc}"
                    ) from exc

    def _estimate_size(self, source: Path) -> tuple[int, int]:
        file_count = 0
        size_bytes = 0
        for root, dirs, files in os.walk(source, followlinks=False):
            relative = Path(root).relative_to(source)
            if relative.parts and relative.parts[0] == ".git":
                dirs[:] = []
                continue
            for name in files:
                try:
                    size_bytes += (Path(root) / name).stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                file_count += 1
        return file_count, size_bytes

    # -- internal: git inspection helpers -----------------------------------------

    async def _is_git_repo(self, source: Path) -> bool:
        returncode, stdout, _ = await self._run_git(
            ["-c", f"safe.directory={source}", "rev-parse", "--is-inside-work-tree"],
            cwd=source,
        )
        return returncode == 0 and stdout.strip() == "true"

    async def _capture_snapshot(self, source: Path) -> tuple[str | None, bool]:
        head_code, head_out, _ = await self._run_git(
            ["-c", f"safe.directory={source}", "rev-parse", "HEAD"], cwd=source
        )
        head = head_out.strip() if head_code == 0 else None
        status_code, status_out, _ = await self._run_git(
            ["-c", f"safe.directory={source}", "status", "--porcelain"], cwd=source
        )
        # A failed status read is treated as dirty, not clean -- this
        # function only ever gets to say "safe to proceed" when it has
        # positive evidence of a clean tree.
        dirty = bool(status_out.strip()) if status_code == 0 else True
        return head, dirty

    async def _has_submodules(self, source: Path) -> bool:
        if await asyncio.to_thread((source / ".gitmodules").is_file):
            return True
        returncode, stdout, _ = await self._run_git(
            ["-c", f"safe.directory={source}", "ls-tree", "-r", "HEAD"], cwd=source
        )
        if returncode != 0:
            return False
        for line in stdout.splitlines():
            fields = line.split(None, 1)
            if fields and fields[0] == "160000":
                return True
        return False

    async def _branch_info(self, source: Path) -> tuple[str | None, bool]:
        returncode, stdout, _ = await self._run_git(
            ["-c", f"safe.directory={source}", "symbolic-ref", "-q", "HEAD"],
            cwd=source,
        )
        if returncode != 0:
            return None, True
        return stdout.strip().removeprefix("refs/heads/"), False

    # -- internal: shared plumbing --------------------------------------------

    def _project_root(self, project_id: str) -> Path:
        # Duplicates GitWorktreeIsolation._project_root's formula
        # (git_worktree.py) rather than importing it -- keeps that file
        # at zero changes for this phase. Must stay in sync by hand if
        # that formula ever changes.
        return self.workspace_root / project_id / "project"

    def _overlaps_workspace(self, resolved_source: Path) -> bool:
        if resolved_source == self.workspace_root:
            return True
        if self.workspace_root in resolved_source.parents:
            return True
        return resolved_source in self.workspace_root.parents

    def _ineligible(
        self,
        *,
        reason: str,
        exists: bool = False,
        is_directory: bool = False,
    ) -> SourceInspection:
        return SourceInspection(
            eligible=False,
            reason=reason,
            exists=exists,
            is_directory=is_directory,
            is_git_repo=False,
            head_commit=None,
            branch=None,
            detached_head=False,
            is_dirty=False,
            has_submodules=False,
            file_count_estimate=None,
            size_bytes_estimate=None,
        )

    async def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = _INSPECT_TIMEOUT_SECONDS,
    ) -> tuple[int, str, str]:
        # --no-optional-locks is unconditional here: it matters for reads
        # against the source (prevents even the optional .git/index
        # stat-refresh a plain `status` would otherwise perform -- the
        # guarantee is zero bytes written, not "zero bytes except an
        # index cache refresh") and is a harmless no-op for the
        # destination-side writes (init/config/commit/branch/remote) this
        # same helper also runs, which are legitimate writes to a copy
        # Agentarium already owns.
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
            raise ImportSourceError(
                f"El comando 'git {args[0] if args else ''}' tardó demasiado"
            ) from None
        return (
            process.returncode if process.returncode is not None else -1,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def _run_git_checked(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = _INSPECT_TIMEOUT_SECONDS,
    ) -> str:
        """`_run_git`, but for a call this method has no fallback for --
        found necessary by hand: `_import_git`/`_import_flat` used to
        call `_run_git` and never look at the returned exit code, so a
        real git failure (a destination path over Windows' MAX_PATH --
        confirmed by hand: git's own clone failed with "Filename too
        long" against a 266-character destination, entirely reproducible,
        not flaky -- a full disk, a locked file, ...) left `destination`
        missing or partial and the *next* command in the sequence crashed
        several frames later with a confusing low-level error instead of
        a clean, expected `ImportSourceError`. Only for calls whose
        failure has no meaning of its own -- `_is_git_repo`,
        `_capture_snapshot`, `_has_submodules`, `_branch_info`, and the
        `symbolic-ref` detached-HEAD check in `_import_git`, all of which
        read a real, expected answer from a non-zero exit -- keep using
        `_run_git` directly."""
        returncode, stdout, stderr = await self._run_git(
            args, cwd=cwd, timeout_seconds=timeout_seconds
        )
        if returncode != 0:
            raise ImportSourceError(
                f"git {' '.join(args)} falló ({returncode}): "
                f"{stderr.strip() or stdout.strip() or 'sin salida'}"
            )
        return stdout
