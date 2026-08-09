from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.execution import WorkspaceFileProposal, WorkspaceMaterializer
from agentarium.isolation import (
    ExportError,
    GitWorktreeIsolation,
    ImportSource,
    IntegrationResult,
    ProjectExporter,
)
from agentarium.isolation.export import ProjectExporter as ProjectExporterClass


def _policy_path() -> Path:
    return project_root() / "configs" / "policies" / "security.yaml"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=True, shell=False
    )


def _init_git_repo(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", f"--initial-branch={branch}"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    _run(["git", "config", "user.email", "test@example.com"], cwd=path)


def _commit_all(path: Path, message: str) -> str:
    _run(["git", "add", "-A"], cwd=path)
    _run(["git", "commit", "-m", message], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


def _rev_parse(path: Path, ref: str = "HEAD") -> str:
    return _run(["git", "rev-parse", ref], cwd=path).stdout.strip()


async def _integrate_text_change(
    isolation: GitWorktreeIsolation,
    materializer: WorkspaceMaterializer,
    project_id: str,
    task_id: str,
    *,
    path: str = "README.md",
    content: str = "# Integrated\n",
) -> IntegrationResult:
    session = await isolation.prepare(project_id, task_id, 1)
    materializer.stage(
        project_id,
        session.path,
        [WorkspaceFileProposal(path=path, content=content, purpose="test fixture")],
    )
    changes = await isolation.collect(session)
    integration = await isolation.integrate(changes)
    await isolation.discard(session)
    return integration


async def _integrate_binary_change(
    isolation: GitWorktreeIsolation,
    project_id: str,
    task_id: str,
    *,
    path: str = "image.bin",
    content: bytes = bytes(range(256)),
) -> IntegrationResult:
    session = await isolation.prepare(project_id, task_id, 1)
    (session.path / path).write_bytes(content)
    changes = await isolation.collect(session)
    integration = await isolation.integrate(changes)
    await isolation.discard(session)
    return integration


# -- never writes to the project's own repository --------------------------


@pytest.mark.asyncio
async def test_export_never_writes_to_the_project_repository(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")
    project_root_path = workspace_root / project_id / "project"
    status_before = _run(["git", "status", "--porcelain"], project_root_path).stdout
    log_before = _run(["git", "log", "--oneline", "--all"], project_root_path).stdout

    await exporter.preview(project_id, base_commit=None)
    destination = tmp_path / "export-out"
    pending = await exporter.export(
        project_id,
        destination,
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )
    await exporter.publish(pending)

    status_after = _run(["git", "status", "--porcelain"], project_root_path).stdout
    log_after = _run(["git", "log", "--oneline", "--all"], project_root_path).stdout
    assert status_before == status_after == ""
    assert log_before == log_after


# -- destination validation --------------------------------------------------


@pytest.mark.asyncio
async def test_export_rejects_destination_overlapping_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")
    kwargs = dict(
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )

    with pytest.raises(ExportError):
        await exporter.export(project_id, workspace_root, **kwargs)

    nested = workspace_root / "already-here"
    nested.mkdir(parents=True)
    with pytest.raises(ExportError):
        await exporter.export(project_id, nested, **kwargs)

    with pytest.raises(ExportError):
        await exporter.export(project_id, tmp_path, **kwargs)


@pytest.mark.asyncio
async def test_export_rejects_relative_destination(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")

    with pytest.raises(ExportError):
        await exporter.export(
            project_id,
            Path("relative/output"),
            base_commit=None,
            imported_source_path=None,
            last_known_integration_commit=integration.commit,
            has_any_integration_event=True,
        )


@pytest.mark.asyncio
async def test_export_rejects_destination_overlapping_imported_source_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    head_before = _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    import_source = ImportSource(workspace_root)
    inspection = await import_source.import_into(source, project_id)
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")

    kwargs = dict(
        base_commit=inspection.head_commit,
        imported_source_path=str(source.resolve()),
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )

    with pytest.raises(ExportError):
        await exporter.export(project_id, source, **kwargs)

    nested = source / "already-here"
    nested.mkdir()
    with pytest.raises(ExportError):
        await exporter.export(project_id, nested, **kwargs)

    # never opened/read/wrote the original while rejecting either attempt
    assert _rev_parse(source) == head_before
    assert _run(["git", "status", "--porcelain"], source).stdout == ""


# -- base commit resolution ---------------------------------------------------


@pytest.mark.asyncio
async def test_export_greenfield_uses_repository_root_as_base(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")
    project_root_path = workspace_root / project_id / "project"
    root_commit = _run(
        ["git", "rev-list", "--max-parents=0", "main"], project_root_path
    ).stdout.strip()

    manifest = await exporter.preview(project_id, base_commit=None)

    assert manifest.base_commit == root_commit
    assert manifest.base_is_import_commit is False
    # 2, not 1: integrate()'s `git merge --no-ff` preserves the task's own
    # commit (from collect()) as a distinct node alongside the merge
    # commit itself -- both are reachable from main and not from the
    # base, so both are legitimately part of the exported range.
    assert manifest.commit_count == 2
    assert manifest.head_commit == integration.commit


@pytest.mark.asyncio
async def test_export_imported_project_uses_imported_commit_as_base(tmp_path: Path) -> None:
    source = tmp_path / "original"
    _init_git_repo(source)
    (source / "original.txt").write_text("original content", encoding="utf-8")
    _commit_all(source, "original work")

    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    import_source = ImportSource(workspace_root)
    inspection = await import_source.import_into(source, project_id)
    await _integrate_text_change(
        isolation, materializer, project_id, "task-1", path="new.txt", content="new\n"
    )

    manifest = await exporter.preview(project_id, base_commit=inspection.head_commit)

    assert manifest.base_commit == inspection.head_commit
    assert manifest.base_is_import_commit is True
    assert manifest.commit_count == 2  # task commit + --no-ff merge commit
    assert "new.txt" in manifest.files_changed
    assert "original.txt" not in manifest.files_changed


# -- clean rejections for real edge cases -------------------------------------


@pytest.mark.asyncio
async def test_export_rejects_project_with_no_repository_yet(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)

    with pytest.raises(ExportError):
        await exporter.preview("never-run-project", base_commit=None)

    with pytest.raises(ExportError):
        await exporter.export(
            "never-run-project",
            tmp_path / "out",
            base_commit=None,
            imported_source_path=None,
            last_known_integration_commit=None,
            has_any_integration_event=False,
        )


@pytest.mark.asyncio
async def test_export_rejects_when_nothing_new_since_import(tmp_path: Path) -> None:
    source = tmp_path / "original"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    import_source = ImportSource(workspace_root)
    inspection = await import_source.import_into(source, project_id)

    with pytest.raises(ExportError):
        await exporter.preview(project_id, base_commit=inspection.head_commit)

    destination = tmp_path / "out"
    with pytest.raises(ExportError):
        await exporter.export(
            project_id,
            destination,
            base_commit=inspection.head_commit,
            imported_source_path=str(source.resolve()),
            last_known_integration_commit=None,
            has_any_integration_event=False,
        )
    assert not destination.exists()


@pytest.mark.asyncio
async def test_export_rejects_when_greenfield_has_nothing_integrated_yet(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    # prepare() alone triggers _ensure_repository() (the empty init
    # commit) without integrating anything -- main sits exactly at the
    # repository root, an empty range.
    session = await isolation.prepare(project_id, "task-1", 1)
    await isolation.discard(session)

    with pytest.raises(ExportError):
        await exporter.preview(project_id, base_commit=None)


# -- patch: git am, including a real binary file ------------------------------


@pytest.mark.asyncio
async def test_export_patch_is_actually_appliable_with_git_am(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    await _integrate_text_change(
        isolation, materializer, project_id, "task-1", path="notes.md", content="notes\n"
    )
    binary_content = bytes(range(256))
    integration = await _integrate_binary_change(
        isolation, project_id, "task-2", path="asset.bin", content=binary_content
    )

    destination = tmp_path / "export-out"
    pending = await exporter.export(
        project_id,
        destination,
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
        formats=frozenset({"patch"}),
    )
    result = await exporter.publish(pending)
    assert result.patch_path is not None

    # Apply the exported patch series into a completely fresh checkout at
    # the base commit -- the real proof that format-patch --binary plus
    # never decoding/re-encoding the captured stdout produced something
    # git am actually accepts, with the binary content byte-identical.
    apply_target = tmp_path / "apply-target"
    project_root_path = workspace_root / project_id / "project"
    _run(["git", "clone", str(project_root_path), str(apply_target)], tmp_path)
    _run(["git", "checkout", result.manifest.base_commit], apply_target)
    _run(["git", "config", "user.name", "Test"], apply_target)
    _run(["git", "config", "user.email", "test@example.com"], apply_target)
    _run(["git", "am", str(result.patch_path)], apply_target)

    assert (apply_target / "notes.md").read_text(encoding="utf-8") == "notes\n"
    assert (apply_target / "asset.bin").read_bytes() == binary_content
    # Not a commit-SHA comparison: `git am` linearizes a replay of each
    # patch, which can never reproduce the original --no-ff merge
    # commit's SHA (different parents, different graph shape) even when
    # the resulting content is byte-identical. The tree hash is the
    # right invariant -- purely a function of file content/structure,
    # independent of commit metadata.
    replayed_tree = _run(["git", "rev-parse", "HEAD^{tree}"], apply_target).stdout.strip()
    real_tree = _run(["git", "rev-parse", "main^{tree}"], project_root_path).stdout.strip()
    assert replayed_tree == real_tree


# -- bundle: fetchable/cloneable ------------------------------------------------


@pytest.mark.asyncio
async def test_export_bundle_is_fetchable_into_a_repo_with_the_base_commit(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")
    destination = tmp_path / "export-out"
    pending = await exporter.export(
        project_id,
        destination,
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
        formats=frozenset({"bundle"}),
    )
    result = await exporter.publish(pending)
    assert result.bundle_path is not None

    # A bundle over an exclusive range (base..main) is "thin": git
    # refuses to `clone` it directly into an empty destination (it
    # lacks the range's prerequisite commit) -- confirmed by hand this
    # fails with "Repository lacks these prerequisite commits", the
    # expected, documented git behaviour for a ranged bundle, not a bug.
    # The real, supported operation is fetching it into a repo that
    # already has the base commit, exactly like a real user's own clone
    # of wherever this project's history actually started would.
    project_root_path = workspace_root / project_id / "project"
    receiving = tmp_path / "receiving-clone"
    _run(["git", "clone", str(project_root_path), str(receiving)], tmp_path)
    _run(["git", "fetch", str(result.bundle_path), "main:agentarium-delivery"], receiving)
    assert _rev_parse(receiving, "agentarium-delivery") == result.manifest.head_commit


@pytest.mark.asyncio
async def test_export_bundle_for_imported_project_is_fetchable_into_the_original(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original"
    _init_git_repo(source)
    (source / "original.txt").write_text("original content", encoding="utf-8")
    _commit_all(source, "original work")

    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    import_source = ImportSource(workspace_root)
    inspection = await import_source.import_into(source, project_id)
    integration = await _integrate_text_change(
        isolation, materializer, project_id, "task-1", path="delivered.txt", content="new\n"
    )

    destination = tmp_path / "export-out"
    pending = await exporter.export(
        project_id,
        destination,
        base_commit=inspection.head_commit,
        imported_source_path=str(source.resolve()),
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
        formats=frozenset({"bundle"}),
    )
    result = await exporter.publish(pending)
    assert result.bundle_path is not None

    status_before = _run(["git", "status", "--porcelain"], source).stdout
    head_before = _rev_parse(source)

    # A separate, real clone of the original -- not the original itself,
    # and not Agentarium's own copy -- is where a user would actually run
    # this fetch from.
    own_clone = tmp_path / "own-clone-of-original"
    _run(["git", "clone", str(source), str(own_clone)], tmp_path)
    _run(
        ["git", "fetch", str(result.bundle_path), "main:agentarium-delivery"],
        own_clone,
    )

    assert _rev_parse(own_clone, "agentarium-delivery") == result.manifest.head_commit
    delivered_content = _run(
        ["git", "show", "agentarium-delivery:delivered.txt"], own_clone
    ).stdout
    assert delivered_content == "new\n"

    # the true original -- never touched, never even opened by this test
    # beyond the git-status/rev-parse calls used to prove exactly that
    assert _run(["git", "status", "--porcelain"], source).stdout == status_before
    assert _rev_parse(source) == head_before


# -- staging / publication ----------------------------------------------------


@pytest.mark.asyncio
async def test_export_writes_into_a_fresh_subdirectory_and_never_collides(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")
    destination = tmp_path / "export-out"
    kwargs = dict(
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )

    first = await exporter.publish(await exporter.export(project_id, destination, **kwargs))
    second = await exporter.publish(await exporter.export(project_id, destination, **kwargs))

    assert first.destination != second.destination
    assert first.destination.is_dir()
    assert second.destination.is_dir()
    assert (first.destination / "changes.patch").is_file()
    assert (second.destination / "changes.patch").is_file()


@pytest.mark.asyncio
async def test_preview_matches_what_export_would_actually_write(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")

    preview_manifest = await exporter.preview(project_id, base_commit=None)
    pending = await exporter.export(
        project_id,
        tmp_path / "export-out",
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )

    assert preview_manifest == pending.manifest


@pytest.mark.asyncio
async def test_export_cleans_up_staging_dir_on_internal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")

    original_run_git_checked = ProjectExporterClass._run_git_checked

    async def failing_on_bundle(self, args, *, cwd, timeout_seconds=30):  # type: ignore[no-untyped-def]
        if "bundle" in args:
            raise ExportError("simulated bundle failure")
        return await original_run_git_checked(self, args, cwd=cwd, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(ProjectExporterClass, "_run_git_checked", failing_on_bundle)

    destination = tmp_path / "export-out"
    with pytest.raises(ExportError):
        await exporter.export(
            project_id,
            destination,
            base_commit=None,
            imported_source_path=None,
            last_known_integration_commit=integration.commit,
            has_any_integration_event=True,
        )

    assert not destination.exists() or list(destination.iterdir()) == []


# -- git/DB alignment check ----------------------------------------------------


@pytest.mark.asyncio
async def test_export_alignment_check_passes_for_a_normal_export(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    integration = await _integrate_text_change(isolation, materializer, project_id, "task-1")

    pending = await exporter.export(
        project_id,
        tmp_path / "export-out",
        base_commit=None,
        imported_source_path=None,
        last_known_integration_commit=integration.commit,
        has_any_integration_event=True,
    )
    result = await exporter.publish(pending)
    assert result.manifest.head_commit == integration.commit


@pytest.mark.asyncio
async def test_export_alignment_check_rejects_stale_last_known_integration_commit(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    await _integrate_text_change(isolation, materializer, project_id, "task-1")

    destination = tmp_path / "export-out"
    with pytest.raises(ExportError, match="no están alineados"):
        await exporter.export(
            project_id,
            destination,
            base_commit=None,
            imported_source_path=None,
            last_known_integration_commit="f" * 40,
            has_any_integration_event=True,
        )
    assert not destination.exists()


@pytest.mark.asyncio
async def test_export_alignment_check_rejects_when_no_integration_event_recorded(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"
    await _integrate_text_change(isolation, materializer, project_id, "task-1")

    with pytest.raises(ExportError, match="no están alineados"):
        await exporter.export(
            project_id,
            tmp_path / "export-out",
            base_commit=None,
            imported_source_path=None,
            last_known_integration_commit=None,
            has_any_integration_event=False,
        )


# -- concurrency ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_serializes_against_concurrent_integrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspaces"
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    exporter = ProjectExporter(workspace_root, isolation)
    project_id = "project"

    await _integrate_text_change(
        isolation, materializer, project_id, "task-1", path="a.txt", content="a\n"
    )

    session = await isolation.prepare(project_id, "task-2", 1)
    materializer.stage(
        project_id,
        session.path,
        [WorkspaceFileProposal(path="b.txt", content="b\n", purpose="fixture")],
    )
    changes = await isolation.collect(session)

    export_holds_lock = asyncio.Event()
    release_export = asyncio.Event()
    original_build_manifest = ProjectExporterClass._build_manifest

    async def blocking_build_manifest(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        export_holds_lock.set()
        await release_export.wait()
        return await original_build_manifest(self, *args, **kwargs)

    monkeypatch.setattr(ProjectExporterClass, "_build_manifest", blocking_build_manifest)

    async def run_export():  # type: ignore[no-untyped-def]
        return await exporter.preview(project_id, base_commit=None)

    async def run_integrate_after_export_has_the_lock():  # type: ignore[no-untyped-def]
        await export_holds_lock.wait()
        integrate_task = asyncio.create_task(isolation.integrate(changes))
        await asyncio.sleep(0.05)
        assert not integrate_task.done()
        release_export.set()
        return await integrate_task

    export_manifest, integration = await asyncio.gather(
        run_export(), run_integrate_after_export_has_the_lock()
    )
    await isolation.discard(session)

    assert export_manifest.commit_count == 2  # task commit + --no-ff merge commit
    assert "a.txt" in export_manifest.files_changed
    assert "b.txt" not in export_manifest.files_changed
    assert integration.commit
