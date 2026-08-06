"""P3.2 -- backup/lock/restore mechanics around the versioned migration in
migrations.py. test_schema_migration.py covers migration *correctness* (the
right columns end up with the right data); this file covers the file-level
guarantees around it: when a backup is (and isn't) taken, that an invalid
backup blocks a migration, that two databases never share a lock or backup
directory, concurrent-process safety, restore, and the hard reject on a
too-new database.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from agentarium.repositories import migrations as migrations_module
from agentarium.repositories.backup import (
    BackupValidationError,
    MigrationFailedError,
    RestoreError,
    _backup_dir,
    _lock_path,
    create_validated_backup,
    restore,
)
from agentarium.repositories.backup import (
    _integrity_ok as _real_integrity_ok,
)
from agentarium.repositories.database import Database
from agentarium.repositories.migrations import CURRENT_SCHEMA_VERSION, SchemaTooNewError
from sqlalchemy import text

_ALL_STEP_COLUMNS = {
    "version",
    "owned_paths_json",
    "shared_component",
    "output_strategy",
    "split_depth",
    "execution_contract_json",
}

# Minimal pre-P3.2 schema: just enough to give work_items something pending.
# Deliberately not imported from test_schema_migration.py -- kept local so
# this file reads standalone; see that file for the exhaustive version with
# realistic backfill data.
_LEGACY_SCHEMA = """
CREATE TABLE projects (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    goal TEXT NOT NULL,
    status VARCHAR(40) NOT NULL,
    progress_percent FLOAT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE TABLE milestones (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    "order" INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE TABLE work_items (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    milestone_id VARCHAR(36) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    assignee_role VARCHAR(40) NOT NULL,
    inputs_json JSON NOT NULL,
    expected_outputs_json JSON NOT NULL,
    acceptance_criteria_json JSON NOT NULL,
    allowed_tools_json JSON NOT NULL,
    authorized_files_json JSON NOT NULL,
    max_attempts INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL,
    risk VARCHAR(20) NOT NULL,
    requires_approval BOOLEAN NOT NULL,
    priority INTEGER NOT NULL,
    status VARCHAR(40) NOT NULL,
    last_error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
"""

_LEGACY_PROJECT = """
INSERT INTO projects (id, title, goal, status, created_at, updated_at)
VALUES ('p1', 'Proyecto legacy', 'Meta', 'draft', '2026-07-31 00:00:00', '2026-07-31 00:00:00')
"""

_LEGACY_WORK_ITEM = """
INSERT INTO work_items (
    id, project_id, milestone_id, title, description, assignee_role,
    inputs_json, expected_outputs_json, acceptance_criteria_json,
    allowed_tools_json, authorized_files_json, max_attempts, attempt_count,
    risk, requires_approval, priority, status, created_at, updated_at
) VALUES (
    'w1', 'p1', 'm1', 'Tarea legacy', 'Descripción', 'implementation_worker',
    '{}', '[]', '[]', '[]', '[]', 3, 0, 'medium', 0, 50, 'ready',
    '2026-07-31 00:00:00', '2026-07-31 00:00:00'
)
"""


def _legacy_db(path: Path) -> Database:
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in _LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        connection.execute(text(_LEGACY_PROJECT))
        connection.execute(text(_LEGACY_WORK_ITEM))
        connection.commit()
    return database


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(str(path))
    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
        return int(version)
    finally:
        connection.close()


def _work_item_columns(path: Path) -> set[str]:
    connection = sqlite3.connect(str(path))
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(work_items)")}
    finally:
        connection.close()


def test_backup_is_not_created_for_a_fresh_database(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()
    assert not _backup_dir(path).exists()


def test_backup_is_not_created_when_already_at_current_version(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.create_all()  # second call: nothing pending
    database.dispose()
    assert not _backup_dir(path).exists()


def test_backup_is_created_when_an_existing_database_has_pending_steps(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    database = _legacy_db(path)
    database.create_all()
    database.dispose()
    backups = list(_backup_dir(path).glob("agentarium-pre-*.db"))
    assert len(backups) == 1


def test_invalid_backup_aborts_before_touching_the_real_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.db"
    _legacy_db(path).dispose()
    monkeypatch.setattr("agentarium.repositories.backup._integrity_ok", lambda _path: False)

    with pytest.raises(BackupValidationError):
        create_validated_backup(path, CURRENT_SCHEMA_VERSION)

    # Nothing left behind: the invalid backup was cleaned up, not trusted.
    assert list(_backup_dir(path).glob("agentarium-pre-*.db")) == []
    # The (aborted) attempt never touched the source database.
    assert "version" not in _work_item_columns(path)


def test_backup_and_lock_paths_are_isolated_between_two_databases(tmp_path: Path) -> None:
    path_a = tmp_path / "a" / "agentarium.db"
    path_b = tmp_path / "b" / "agentarium.db"
    path_a.parent.mkdir()
    path_b.parent.mkdir()

    assert _lock_path(path_a) != _lock_path(path_b)
    assert _backup_dir(path_a) != _backup_dir(path_b)

    database_a = _legacy_db(path_a)
    database_b = _legacy_db(path_b)
    database_a.create_all()
    database_b.create_all()
    database_a.dispose()
    database_b.dispose()

    backups_a = list(_backup_dir(path_a).glob("agentarium-pre-*.db"))
    backups_b = list(_backup_dir(path_b).glob("agentarium-pre-*.db"))
    assert len(backups_a) == 1
    assert len(backups_b) == 1
    assert backups_a[0].parent != backups_b[0].parent


def test_concurrent_migration_of_the_same_legacy_database_never_duplicates_a_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    _legacy_db(path).dispose()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _migrate() -> None:
        try:
            barrier.wait(timeout=5)
            database = Database(f"sqlite:///{path}")
            database.create_all()
            database.dispose()
        except BaseException as exc:  # noqa: BLE001 - reported by the main thread, not lost
            errors.append(exc)

    threads = [threading.Thread(target=_migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "migration thread did not finish in time"

    assert not errors, errors
    assert _user_version(path) == CURRENT_SCHEMA_VERSION
    assert _ALL_STEP_COLUMNS <= _work_item_columns(path)


def test_restore_preserves_the_failed_database_and_clears_stale_wal_sidecars(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agentarium.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()

    backup_path = create_validated_backup(path, CURRENT_SCHEMA_VERSION)

    # Simulate a corrupted live database with stale WAL sidecars still next
    # to it -- exactly what "copy the .db file over it" would mishandle.
    path.write_bytes(b"not a real sqlite file")
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    wal.write_bytes(b"stale wal")
    shm.write_bytes(b"stale shm")

    restore(path, backup_path)

    assert not wal.exists()
    assert not shm.exists()
    failed = list(path.parent.glob(f"{path.name}.failed-*"))
    assert len(failed) == 1
    assert failed[0].read_bytes() == b"not a real sqlite file"

    connection = sqlite3.connect(str(path))
    try:
        (result,) = connection.execute("PRAGMA integrity_check").fetchone()
        assert result == "ok"
    finally:
        connection.close()


def test_restore_refuses_an_invalid_backup_without_touching_the_live_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agentarium.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()
    original_bytes = path.read_bytes()

    bad_backup = tmp_path / "bad-backup.db"
    bad_backup.write_bytes(b"not a real sqlite file")

    with pytest.raises(RestoreError):
        restore(path, bad_backup)

    assert path.read_bytes() == original_bytes
    assert list(path.parent.glob(f"{path.name}.failed-*")) == []


def test_a_failure_partway_through_migration_stops_at_the_last_committed_step_and_is_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.db"
    _legacy_db(path).dispose()

    sabotage_column = "split_depth"  # step 5 of 6: a real prefix commits, one is left pending
    original_apply = migrations_module.MigrationStep.apply

    def _sabotaged_apply(
        self: migrations_module.MigrationStep, connection: sqlite3.Connection
    ) -> None:
        if self.column == sabotage_column:
            raise RuntimeError("simulated failure mid-migration")
        original_apply(self, connection)

    monkeypatch.setattr(migrations_module.MigrationStep, "apply", _sabotaged_apply)

    database = Database(f"sqlite:///{path}")
    with pytest.raises(MigrationFailedError) as excinfo:
        database.create_all()
    database.dispose()

    error = excinfo.value
    assert error.backup_path.exists()
    assert str(error.backup_path) in str(error)
    assert "agentarium db restore" in str(error)

    # Steps before the sabotaged one (version, owned_paths_json,
    # shared_component, output_strategy = versions 1-4) committed for real;
    # the sabotaged one and everything after did not.
    assert _user_version(path) == 4
    columns = _work_item_columns(path)
    assert "output_strategy" in columns
    assert "split_depth" not in columns
    assert "execution_contract_json" not in columns

    monkeypatch.undo()  # lift the sabotage before retrying

    database = Database(f"sqlite:///{path}")
    database.create_all()  # idempotent retry -- no manual restore involved
    database.dispose()

    assert _user_version(path) == CURRENT_SCHEMA_VERSION
    assert _ALL_STEP_COLUMNS <= _work_item_columns(path)


def test_a_database_newer_than_this_code_is_rejected_without_writing_anything(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agentarium.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()

    connection = sqlite3.connect(str(path))
    try:
        connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
    finally:
        connection.close()

    before = _work_item_columns(path)

    database = Database(f"sqlite:///{path}")
    with pytest.raises(SchemaTooNewError):
        database.create_all()
    database.dispose()

    assert _user_version(path) == CURRENT_SCHEMA_VERSION + 1
    assert _work_item_columns(path) == before
    assert not _backup_dir(path).exists()


def test_a_too_new_database_missing_a_table_gets_zero_mutation(tmp_path: Path) -> None:
    """reject_if_too_new must gate *every* mutation, including
    Base.metadata.create_all()'s additive table creation -- not just the
    versioned work_items steps. A too-new database missing a table this
    code still knows about (simulating a future schema that dropped it)
    must come back exactly as it was: the table must not be silently
    recreated before the version is even checked."""
    path = tmp_path / "agentarium.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()

    connection = sqlite3.connect(str(path))
    try:
        connection.execute("DROP TABLE approval_requests")
        connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
        connection.commit()
    finally:
        connection.close()

    database = Database(f"sqlite:///{path}")
    with pytest.raises(SchemaTooNewError):
        database.create_all()
    database.dispose()

    connection = sqlite3.connect(str(path))
    try:
        recreated = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='approval_requests'"
        ).fetchone()
    finally:
        connection.close()
    assert recreated is None, "Base.metadata.create_all() ran before the version was checked"
    assert _user_version(path) == CURRENT_SCHEMA_VERSION + 1
    assert not _backup_dir(path).exists()


def test_restore_refuses_when_another_connection_still_has_the_database_open(
    tmp_path: Path,
) -> None:
    """The migration FileLock only serializes upgrade()/restore() calls
    against each other -- it says nothing about an already-running
    Agentarium process (e.g. the API server) that opened this database
    through the ordinary application path and simply never closed its
    connection. restore() must detect that without the other process
    cooperating in any way, and refuse before touching any file."""
    path = tmp_path / "agentarium.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.dispose()

    backup_path = create_validated_backup(path, CURRENT_SCHEMA_VERSION)

    # An open read transaction on a second, independent connection --
    # exactly what a live application connection looks like from the
    # outside, with no cooperation from that process required.
    reader = sqlite3.connect(str(path))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM projects").fetchone()

    original_bytes = path.read_bytes()
    try:
        with pytest.raises(RestoreError):
            restore(path, backup_path)

        assert path.read_bytes() == original_bytes
        assert list(path.parent.glob(f"{path.name}.failed-*")) == []
    finally:
        reader.close()


def test_a_concurrent_write_after_the_backup_snapshot_does_not_invalidate_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_validated_backup must not compare the backup's row counts
    against the *source*'s row count read after VACUUM INTO already took
    its snapshot: a write landing in that window is unrelated to whether
    the backup itself is valid, and must not turn a good backup into a
    reported failure."""
    path = tmp_path / "legacy.db"
    _legacy_db(path).dispose()

    def _integrity_ok_and_then_write_to_source(check_path: Path) -> bool:
        # Called on the *backup* file, right after VACUUM INTO captured its
        # snapshot -- inject a write to the *source* in that exact window,
        # before create_validated_backup finishes validating.
        writer = sqlite3.connect(str(path))
        writer.execute(
            "INSERT INTO projects (id, title, goal, status, created_at, updated_at) "
            "VALUES ('concurrent', 'x', 'y', 'draft', "
            "'2026-08-06 00:00:00', '2026-08-06 00:00:00')"
        )
        writer.commit()
        writer.close()
        return _real_integrity_ok(check_path)

    monkeypatch.setattr(
        "agentarium.repositories.backup._integrity_ok",
        _integrity_ok_and_then_write_to_source,
    )

    backup_path = create_validated_backup(path, CURRENT_SCHEMA_VERSION)

    assert backup_path.exists()
