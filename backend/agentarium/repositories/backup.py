"""SQLite file-level operations for P3.2: backup before a migration that
touches schema/data, and restoring from one. Deliberately separate from
migrations.py -- this module's concerns are files and cross-process
concurrency; migrations.py's are schema-version logic. Both are needed
together only in `upgrade()`, the delegate `Database.create_all()` calls.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from sqlalchemy import text

from . import migrations
from .database import SQLITE_BUSY_TIMEOUT_MS, Base, Database

_LOCK_TIMEOUT_SECONDS = 30
_RESTORE_LOCK_TIMEOUT_SECONDS = 5
_SANITY_TABLES = ("projects", "work_items")


class BackupValidationError(RuntimeError):
    """A freshly-created backup failed validation; nothing was migrated."""


class MigrationFailedError(RuntimeError):
    def __init__(self, db_path: Path, backup_path: Path) -> None:
        self.db_path = db_path
        self.backup_path = backup_path
        super().__init__(
            "La migración de la base de datos falló a mitad de camino.\n"
            f"Backup previo a la migración: {backup_path}\n"
            "Primero probá reintentar (la migración es idempotente y esto "
            "alcanza si la causa fue transitoria, p. ej. disco lleno):\n"
            "    agentarium init\n"
            "Si eso no alcanza, restaurá el backup:\n"
            f'    agentarium db restore "{backup_path}"'
        )


class RestoreError(RuntimeError):
    pass


def sqlite_path_from_url(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url.removeprefix(prefix)
    if not raw or raw == ":memory:":
        return None
    return Path(raw).resolve()


def _lock_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".lock")


def _backup_dir(db_path: Path) -> Path:
    return db_path.parent / "backups"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


def _integrity_ok(path: Path) -> bool:
    try:
        connection = sqlite3.connect(str(path))
        try:
            (result,) = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result == "ok")
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        # Not just a corrupt-but-readable database (that comes back as a
        # non-"ok" integrity_check row): sqlite3 raises this when the file
        # isn't a SQLite database at all, e.g. a 0-byte file or garbage --
        # exactly the kind of "backup" that must fail validation, not crash
        # the caller.
        return False


def _row_counts_match(source: Path, backup: Path) -> bool:
    source_conn = sqlite3.connect(str(source))
    backup_conn = sqlite3.connect(str(backup))
    try:
        for table in _SANITY_TABLES:
            source_count = source_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            backup_count = backup_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if source_count != backup_count:
                return False
        return True
    finally:
        source_conn.close()
        backup_conn.close()


def create_validated_backup(db_path: Path, target_version: int) -> Path:
    """VACUUM INTO a fresh backup file and validate it before trusting it --
    a backup that fails its own integrity check protects nothing."""
    backup_dir = _backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"agentarium-pre-v{target_version}-{_timestamp()}.db"

    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()

    if not _integrity_ok(target) or not _row_counts_match(db_path, target):
        target.unlink(missing_ok=True)
        raise BackupValidationError(
            f"El backup generado en {target} no pasó la validación "
            "(integrity_check o conteo de filas) tras VACUUM INTO. No se "
            "aplicó ninguna migración; la base original sigue intacta."
        )
    return target


def restore(db_path: Path, backup_path: Path) -> None:
    """Restore `db_path` from `backup_path`. Never overwrites a live WAL
    database by copying blindly: acquires the same lock `upgrade()` uses (as
    a proxy for "no other Agentarium process is using this database"),
    validates the backup first, preserves the current file instead of
    deleting it, and clears stale -wal/-shm sidecars that would otherwise
    make SQLite try to replay a WAL that doesn't belong to the restored
    file."""
    lock_path = _lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=_RESTORE_LOCK_TIMEOUT_SECONDS)
    except Timeout as exc:
        raise RestoreError(
            f"No se pudo obtener el lock de {db_path} ({lock_path}). "
            "¿Hay otro proceso de Agentarium usando esta base de datos? "
            "Cerralo antes de restaurar."
        ) from exc

    try:
        if not backup_path.exists() or not _integrity_ok(backup_path):
            raise RestoreError(
                f"El backup {backup_path} no existe o no pasó "
                "PRAGMA integrity_check. No se tocó la base de datos activa."
            )

        failed_path: Path | None = None
        if db_path.exists():
            failed_path = db_path.with_name(f"{db_path.name}.failed-{_timestamp()}")
            shutil.move(str(db_path), str(failed_path))

        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

        shutil.copy2(backup_path, db_path)

        if not _integrity_ok(db_path):
            detail = f" La base anterior se conservó en {failed_path}." if failed_path else ""
            raise RestoreError(
                f"La base restaurada desde {backup_path} no pasó "
                f"PRAGMA integrity_check tras la copia.{detail}"
            )
    finally:
        lock.release()


def upgrade(database: Database) -> None:
    """Delegate of `Database.create_all()` for SQLite. Always creates
    whatever tables the ORM knows about and the file doesn't yet (cheap,
    additive, safe on every call); a genuinely new database gets stamped
    straight at CURRENT_SCHEMA_VERSION since `create_all()` just built it at
    the current shape. An existing database goes through the version-aware
    path: reject outright if it's newer than this code, reconcile a legacy
    (user_version == 0) database against its real schema, then back up and
    apply whatever steps are still pending."""
    db_path = sqlite_path_from_url(database.url)
    if db_path is None:
        # No real file to lock or back up (e.g. sqlite:///:memory:) -- no
        # other process can share this connection's storage, so there is
        # nothing to coordinate and nothing to protect.
        Base.metadata.create_all(database.engine)
        with database.engine.begin() as connection:
            connection.execute(text(f"PRAGMA user_version={migrations.CURRENT_SCHEMA_VERSION}"))
        return

    lock_path = _lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_SECONDS):
        with database.engine.connect() as probe:
            existed_before = (
                probe.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_items'")
                ).first()
                is not None
            )

        Base.metadata.create_all(database.engine)

        if not existed_before:
            with database.engine.begin() as connection:
                connection.execute(
                    text(f"PRAGMA user_version={migrations.CURRENT_SCHEMA_VERSION}")
                )
            return

        raw = sqlite3.connect(str(db_path), isolation_level=None)
        raw.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        try:
            version = migrations.read_user_version(raw)
            migrations.reject_if_too_new(version)

            # Bootstrap case: a legacy (version == 0) database that, once its
            # real schema is inspected, turns out to already carry every
            # known step (the common case -- `_ensure_work_item_columns` has
            # run on every `create_all()` since before this mechanism
            # existed). Nothing to back up or migrate, just reconcile the
            # stored number so future startups take the cheap version-only
            # path instead of re-scanning the schema every time.
            pending = migrations.pending_steps(raw, version)
            if not pending:
                if version != migrations.CURRENT_SCHEMA_VERSION:
                    migrations.write_user_version(raw, migrations.CURRENT_SCHEMA_VERSION)
                return

            backup_path = create_validated_backup(db_path, migrations.CURRENT_SCHEMA_VERSION)
            try:
                for step in pending:
                    migrations.apply_step(raw, step)
            except Exception as exc:
                raise MigrationFailedError(db_path, backup_path) from exc
        finally:
            raw.close()
