from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.isolation import GitWorktreeIsolation, ImportSource, ImportSourceError
from agentarium.isolation.import_source import ImportSource as ImportSourceClass


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


def _config_get(path: Path, key: str) -> str:
    return _run(["git", "config", "--get", key], cwd=path).stdout.strip()


def _remotes(path: Path) -> str:
    return _run(["git", "remote"], cwd=path).stdout.strip()


def _try_create_junction(link: Path, target: Path) -> bool:
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _hash_tree(root: Path) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            contents[str(path.relative_to(root))] = path.read_bytes()
    return contents


# -- source-never-written, the highest-priority guarantee ----------------------


@pytest.mark.asyncio
async def test_import_never_writes_to_the_original_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    head_before = _commit_all(source, "initial")
    tree_before = _hash_tree(source)

    import_source = ImportSource(tmp_path / "workspaces")
    result = await import_source.import_into(source, "project")

    assert result.eligible
    assert _rev_parse(source) == head_before
    assert _hash_tree(source) == tree_before


@pytest.mark.asyncio
async def test_import_refuses_dirty_git_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")
    (source / "file.txt").write_text("v2 -- uncommitted", encoding="utf-8")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(source, "project")

    assert not (workspace_root / "project").exists()
    assert (source / "file.txt").read_text(encoding="utf-8") == "v2 -- uncommitted"


@pytest.mark.asyncio
async def test_import_detects_and_rejects_source_modified_during_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    original_run_git = ImportSourceClass._run_git

    async def mutating_run_git(self, args, *, cwd, timeout_seconds=30):  # type: ignore[no-untyped-def]
        result = await original_run_git(self, args, cwd=cwd, timeout_seconds=timeout_seconds)
        if "clone" in args:
            # Simulate a concurrent edit landing in the source exactly
            # between the clone finishing and the post-clone snapshot
            # check that is supposed to catch it.
            (source / "file.txt").write_text("v2 -- changed mid-clone", encoding="utf-8")
            _commit_all(source, "sneaky change")
        return result

    monkeypatch.setattr(ImportSourceClass, "_run_git", mutating_run_git)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(source, "project")

    assert not (workspace_root / "project").exists()


# -- overlap / absolute-path rejection ------------------------------------------


@pytest.mark.asyncio
async def test_import_rejects_source_overlapping_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    import_source = ImportSource(workspace_root)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(workspace_root, "project")

    nested = workspace_root / "already-here"
    nested.mkdir()
    with pytest.raises(ImportSourceError):
        await import_source.import_into(nested, "project")

    ancestor = tmp_path
    with pytest.raises(ImportSourceError):
        await import_source.import_into(ancestor, "project")


@pytest.mark.asyncio
async def test_inspect_reports_overlapping_source_as_ineligible(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    import_source = ImportSource(workspace_root)

    inspection = await import_source.inspect(workspace_root)

    assert not inspection.eligible
    assert inspection.reason is not None


@pytest.mark.asyncio
async def test_inspect_reports_relative_path_as_ineligible(tmp_path: Path) -> None:
    import_source = ImportSource(tmp_path / "workspaces")

    inspection = await import_source.inspect(Path("relative/path"))

    assert not inspection.eligible
    assert inspection.reason is not None


# -- submodule detection ---------------------------------------------------------


@pytest.mark.asyncio
async def test_import_rejects_git_source_with_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submodule_source = tmp_path / "submodule-source"
    _init_git_repo(submodule_source)
    (submodule_source / "lib.txt").write_text("lib", encoding="utf-8")
    _commit_all(submodule_source, "submodule initial")

    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "app.txt").write_text("app", encoding="utf-8")
    _commit_all(source, "app initial")
    _run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(submodule_source),
            "lib",
        ],
        cwd=source,
    )
    _commit_all(source, "add submodule")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)

    calls: list[list[str]] = []
    original_run_git = ImportSourceClass._run_git

    async def recording_run_git(self, args, *, cwd, timeout_seconds=30):  # type: ignore[no-untyped-def]
        calls.append(args)
        return await original_run_git(self, args, cwd=cwd, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(ImportSourceClass, "_run_git", recording_run_git)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(source, "project")

    assert not any("--recurse-submodules" in call for call in calls)
    assert not any("clone" in call for call in calls)


@pytest.mark.asyncio
async def test_inspect_reports_submodules_as_ineligible(tmp_path: Path) -> None:
    submodule_source = tmp_path / "submodule-source"
    _init_git_repo(submodule_source)
    (submodule_source / "lib.txt").write_text("lib", encoding="utf-8")
    _commit_all(submodule_source, "submodule initial")

    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "app.txt").write_text("app", encoding="utf-8")
    _commit_all(source, "app initial")
    _run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(submodule_source),
            "lib",
        ],
        cwd=source,
    )
    _commit_all(source, "add submodule")

    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(source)

    assert not inspection.eligible
    assert inspection.has_submodules
    assert inspection.reason is not None


# -- the autocrlf/longpaths regression, and the --no-local guarantee -----------


@pytest.mark.asyncio
async def test_import_sets_autocrlf_false_and_longpaths_true_on_destination_via_clone(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    await import_source.import_into(source, "project")

    destination = workspace_root / "project" / "project"
    assert _config_get(destination, "core.autocrlf") == "false"
    assert _config_get(destination, "core.longpaths") == "true"


@pytest.mark.asyncio
async def test_import_sets_autocrlf_false_and_longpaths_true_on_destination_via_flat_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("v1", encoding="utf-8")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    await import_source.import_into(source, "project")

    destination = workspace_root / "project" / "project"
    assert _config_get(destination, "core.autocrlf") == "false"
    assert _config_get(destination, "core.longpaths") == "true"


@pytest.mark.asyncio
async def test_import_clones_with_no_local_and_preserves_commit_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    head_before = _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)

    clone_calls: list[list[str]] = []
    original_run_git = ImportSourceClass._run_git

    async def recording_run_git(self, args, *, cwd, timeout_seconds=30):  # type: ignore[no-untyped-def]
        if "clone" in args:
            clone_calls.append(args)
        return await original_run_git(self, args, cwd=cwd, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(ImportSourceClass, "_run_git", recording_run_git)

    result = await import_source.import_into(source, "project")

    assert len(clone_calls) == 1
    assert "--no-local" in clone_calls[0]
    assert result.head_commit == head_before
    destination = workspace_root / "project" / "project"
    assert _rev_parse(destination) == head_before
    assert _remotes(destination) == ""


# -- branch handling ---------------------------------------------------------


@pytest.mark.asyncio
async def test_import_renames_non_main_default_branch_and_prepare_still_works(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_git_repo(source, branch="master")
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    await import_source.import_into(source, "project")

    destination = workspace_root / "project" / "project"
    current_branch = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=destination
    ).stdout.strip()
    assert current_branch == "main"

    # End-to-end proof, not just inspection: GitWorktreeIsolation itself
    # needed zero changes for this to keep working -- it just needs a ref
    # named "main" to exist, which the rename above already guaranteed.
    isolation = GitWorktreeIsolation(workspace_root, _policy_path())
    session = await isolation.prepare("project", "task", 1)
    assert session.path.exists()
    await isolation.discard(session)


@pytest.mark.asyncio
async def test_import_handles_detached_head_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    first_commit = _commit_all(source, "first")
    (source / "file.txt").write_text("v2", encoding="utf-8")
    _commit_all(source, "second")
    _run(["git", "checkout", first_commit], cwd=source)

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    result = await import_source.import_into(source, "project")

    assert result.head_commit == first_commit
    destination = workspace_root / "project" / "project"
    current_branch = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=destination
    ).stdout.strip()
    assert current_branch == "main"


# -- plain-folder path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_import_copies_plain_folder_and_git_inits(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "top.txt").write_text("top", encoding="utf-8")
    (source / "nested" / "inner.txt").write_text("inner", encoding="utf-8")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)
    result = await import_source.import_into(source, "project")

    assert result.eligible
    destination = workspace_root / "project" / "project"
    assert (destination / "top.txt").read_text(encoding="utf-8") == "top"
    assert (destination / "nested" / "inner.txt").read_text(encoding="utf-8") == "inner"
    assert _rev_parse(destination)
    assert not (source / ".git").exists()


@pytest.mark.asyncio
async def test_import_rejects_flat_folder_with_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    outside_target = tmp_path / "outside"
    junction = source / "escape"
    if not _try_create_junction(junction, outside_target):
        pytest.skip("No se pudo crear una junction en este entorno")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(source, "project")

    assert not (workspace_root / "project").exists()


@pytest.mark.asyncio
async def test_import_aborts_on_unreadable_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "good.txt").write_text("good", encoding="utf-8")
    (source / "bad.txt").write_text("bad", encoding="utf-8")

    workspace_root = tmp_path / "workspaces"
    import_source = ImportSource(workspace_root)

    import shutil as shutil_module

    original_copy2 = shutil_module.copy2

    def failing_copy2(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        if Path(src).name == "bad.txt":
            raise OSError("simulated permission error")
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil_module, "copy2", failing_copy2)

    with pytest.raises(ImportSourceError):
        await import_source.import_into(source, "project")

    assert not (workspace_root / "project").exists()


# -- inspect(): never raises for an expected outcome ----------------------------


@pytest.mark.asyncio
async def test_inspect_reports_plain_non_git_folder(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")

    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(source)

    assert inspection.eligible
    assert not inspection.is_git_repo


@pytest.mark.asyncio
async def test_inspect_reports_clean_git_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    head = _commit_all(source, "initial")

    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(source)

    assert inspection.eligible
    assert inspection.is_git_repo
    assert inspection.head_commit == head
    assert inspection.branch == "main"
    assert not inspection.is_dirty
    assert not inspection.detached_head


@pytest.mark.asyncio
async def test_inspect_reports_dirty_git_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)
    (source / "file.txt").write_text("v1", encoding="utf-8")
    _commit_all(source, "initial")
    (source / "untracked.txt").write_text("new", encoding="utf-8")

    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(source)

    assert not inspection.eligible
    assert inspection.is_dirty
    assert inspection.reason is not None


@pytest.mark.asyncio
async def test_inspect_reports_missing_path(tmp_path: Path) -> None:
    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(tmp_path / "does-not-exist")

    assert not inspection.eligible
    assert not inspection.exists


@pytest.mark.asyncio
async def test_inspect_reports_zero_commit_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_git_repo(source)

    import_source = ImportSource(tmp_path / "workspaces")
    inspection = await import_source.inspect(source)

    assert inspection.is_git_repo
    assert inspection.head_commit is None
