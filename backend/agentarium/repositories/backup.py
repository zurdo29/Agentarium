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


def _backup_tables_are_queryable(backup: Path) -> bool:
    """Confirms the sanity tables exist and are readable in the backup, on
    the backup's own terms only.

    Deliberately does not compare row counts against the *source*: the
    source is a live, mutable database that a concurrent writer (another
    Agentarium process, a request the API is still handling) can keep
    changing after `VACUUM INTO` has already taken its snapshot. Comparing
    against the source's row count *after* the fact means a write in that
    window makes a perfectly valid backup look invalid -- a false failure
    with nothing wrong with the backup itself. `PRAGMA integrity_check`
    already validates the backup's own internal structure deeply; this adds
    the one thing it doesn't cover -- that the specific tables this
    mechanism cares about are actually present and queryable, not just that
    whatever pages exist are structurally sound.
    """
    connection = sqlite3.connect(str(backup))
    try:
        for table in _SANITY_TABLES:
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


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

    if not _integrity_ok(target) or not _backup_tables_are_queryable(target):
        target.unlink(missing_ok=True)
        raise BackupValidationError(
            f"El backup generado en {target} no pasó la validación "
            "(integrity_check o tablas de referencia) tras VACUUM INTO. No "
            "se aplicó ninguna migración; la base original sigue intacta."
        )
    return target


def _ensure_no_active_connection(db_path: Path) -> None:
    """The migration `FileLock` only proves no other `upgrade()`/`restore()`
    call is running concurrently -- it says nothing about an already-running
    Agentarium process (e.g. the API server) that opened this database
    normally, long before this `restore()` call, and never closed it.
    Deleting `-wal`/`-shm` or replacing the file under a live connection can
    corrupt whatever that connection does next.

    This is the minimal offline check available without the other process
    cooperating: rewrite `PRAGMA user_version` to its own current value (a
    real write, but a semantic no-op -- nothing about the database's
    meaning changes) to guarantee a fresh WAL frame exists, then attempt a
    TRUNCATE checkpoint. A TRUNCATE checkpoint can only fully succeed --
    reducing the WAL to zero frames -- when no other connection holds an
    open read snapshot or write lock. Verified empirically before relying
    on it: a reader with an open transaction, even one that had read
    nothing new since a subsequent write, reliably makes the checkpoint
    come back with `busy=1` and 0 of the pending frames checkpointed.
    """
    connection = sqlite3.connect(str(db_path), timeout=2.0)
    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
        connection.execute(f"PRAGMA user_version={int(version)}")
        connection.commit()
        busy, log_frames, checkpointed_frames = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise RestoreError(
            f"No se pudo verificar que ninguna conexión siga usando {db_path}: "
            f"{exc}. Cerrá cualquier proceso de Agentarium que la tenga "
            "abierta antes de restaurar."
        ) from exc
    except sqlite3.DatabaseError:
        # Not a valid SQLite database at all (sqlite3's "file is not a
        # database", e.g. db_path is corrupted -- precisely the scenario
        # restore() exists to recover from). There is no possible live WAL
        # connection to protect against a file that was never validly
        # opened as a database in the first place; safe to proceed.
        # OperationalError (above) is a subclass of DatabaseError and is
        # handled first -- that one *is* a live, contentious database this
        # check cannot see past, so it still refuses.
        return
    finally:
        connection.close()

    # log_frames == -1 means the database isn't in WAL mode at all (nothing
    # this check protects against): every real Agentarium database is,
    # unconditionally, from its very first connection.
    if busy or (log_frames >= 0 and checkpointed_frames < log_frames):
        raise RestoreError(
            f"{db_path} todavía parece tener una conexión activa (checkpoint "
            f"WAL incompleto: busy={busy}, {checkpointed_frames}/{log_frames} "
            "frames pendientes). Cerrá cualquier proceso de Agentarium que "
            "la tenga abierta -- la API, un `agentarium project run`, etc. "
            "-- antes de restaurar."
        )


def restore(db_path: Path, backup_path: Path) -> None:
    """Restore `db_path` from `backup_path`. Never overwrites a live WAL
    database by copying blindly: acquires the same lock `upgrade()` uses,
    validates the backup, confirms no other connection still has `db_path`
    open (`_ensure_no_active_connection` -- the lock alone does not prove
    this, see its docstring), preserves the current file instead of
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
            _ensure_no_active_connection(db_path)
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
    """Delegate of `Database.create_all()` for SQLite. A genuinely new
    database (nothing existed before this call) is built freely at the
    current shape. An existing database gets zero mutation of any kind --
    not even `Base.metadata.create_all()`'s additive table creation, and not
    even the `PRAGMA journal_mode=WAL` a first SQLAlchemy connection would
    set -- until `reject_if_too_new` has confirmed this code isn't older
    than whatever already migrated it. Only past that gate does table
    creation, backup, and migration happen, in that order."""
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
        # A plain sqlite3 connection, not the SQLAlchemy engine: the engine's
        # first connection to this file fires _configure_sqlite_connection,
        # which sets PRAGMA journal_mode=WAL -- itself a real mutation on a
        # database that isn't already in WAL mode. Nothing touches the
        # engine (including Base.metadata.create_all()) until
        # reject_if_too_new has cleared this database.
        raw = sqlite3.connect(str(db_path), isolation_level=None)
        raw.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        try:
            existed_before = (
                raw.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_items'"
                ).fetchone()
                is not None
            )

            if not existed_before:
                # Genuinely new database: nothing to protect. Build freely
                # at the current shape and stamp the version directly.
                Base.metadata.create_all(database.engine)
                with database.engine.begin() as connection:
                    connection.execute(
                        text(f"PRAGMA user_version={migrations.CURRENT_SCHEMA_VERSION}")
                    )
                return

            version = migrations.read_user_version(raw)
            migrations.reject_if_too_new(version)

            # Past the gate: this code is not older than whatever already
            # migrated this database, so it's now safe to let SQLAlchemy
            # touch it (e.g. create a table added by a later version of this
            # same code that this database predates).
            Base.metadata.create_all(database.engine)

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
            # `pending` only ever holds a subset of STEPS -- whatever the
            # loop above just applied plus whatever satisfied() already
            # found true. Once it completes without error every step is
            # satisfied, by construction, regardless of which one happened
            # to be last in `pending`: stamp CURRENT_SCHEMA_VERSION directly
            # rather than trusting the final apply_step()'s own version,
            # which can be lower when a later step was already satisfied
            # (see migrations.pending_steps).
            migrations.write_user_version(raw, migrations.CURRENT_SCHEMA_VERSION)
        finally:
            raw.close()
