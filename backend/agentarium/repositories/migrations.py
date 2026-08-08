"""Versioned schema evolution for SQLite tables altered after creation (see
git history on tables.py: b966286, d5588c4, 28bc225 -- all ADD COLUMN,
never a rename/drop). Every step targets exactly one table (`work_items`
for steps 1-6, `projects` starting with step 7, P3.4/ADR 0034).

Pure schema logic: every function here takes a raw `sqlite3.Connection`,
never a SQLAlchemy engine/session. `backup.py` owns the file/lock/backup
concerns and calls into this module while holding both.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


class SchemaTooNewError(RuntimeError):
    """A database's user_version is newer than this code understands."""


@dataclass(frozen=True)
class MigrationStep:
    version: int
    column: str
    definition: str
    backfill: tuple[str, ...] = field(default_factory=tuple)
    # A SELECT COUNT(*) that returns 0 exactly when this step's backfill has
    # nothing left to do -- the *data* postcondition the backfill exists to
    # guarantee, not just "the column exists". None for steps with no
    # backfill, where column presence alone is the whole contract.
    postcondition: str | None = None
    # Always one of this module's own literals ("work_items", "projects"),
    # never user input -- same invariant write_user_version already relies
    # on for its own f-string interpolation below.
    table: str = "work_items"

    def satisfied(self, connection: sqlite3.Connection) -> bool:
        if self.column not in _table_columns(connection, self.table):
            return False
        if self.postcondition is None:
            return True
        try:
            (remaining,) = connection.execute(self.postcondition).fetchone()
        except sqlite3.OperationalError:
            # The postcondition itself references a column another,
            # still-pending step is responsible for adding (e.g. this step's
            # backfill matches on shared_component, added by an earlier
            # step) -- definitely not satisfied yet.
            return False
        return bool(remaining == 0)

    def apply(self, connection: sqlite3.Connection) -> None:
        # Idempotent regardless of why apply() is being called: a step
        # missing its column always needs the ALTER, but a step whose column
        # already exists with a pending backfill (satisfied() said False via
        # the postcondition, not via column absence) must skip straight to
        # the backfill -- ALTER TABLE ADD COLUMN on an existing column is a
        # hard error, not a no-op.
        if self.column not in _table_columns(connection, self.table):
            connection.execute(
                f"ALTER TABLE {self.table} ADD COLUMN {self.column} {self.definition}"
            )
        for statement in self.backfill:
            connection.execute(statement)


# Ported from the pre-P3.2 `_WORK_ITEM_COLUMN_MIGRATIONS`/
# `_WORK_ITEM_COLUMN_BACKFILLS` tuples in database.py -- same columns, same
# order, same backfill, now with an explicit version number per step instead
# of an implicit "run the whole tuple in order" contract.
STEPS: tuple[MigrationStep, ...] = (
    MigrationStep(1, "version", "INTEGER NOT NULL DEFAULT 1"),
    MigrationStep(2, "owned_paths_json", "JSON NOT NULL DEFAULT '[]'"),
    MigrationStep(3, "shared_component", "VARCHAR(120)"),
    MigrationStep(4, "output_strategy", "VARCHAR(20) NOT NULL DEFAULT 'exclusive'"),
    # A plain `DEFAULT 0` would tell every task in an existing database that
    # it descends from no split, including the subtasks and consolidations a
    # previous version already created -- which would let those split a
    # second time, the exact recursion `split_depth` exists to stop. This
    # backfill runs inside the same transaction as the ALTER, matching on the
    # literal title prefixes and the `split-<id>` sharing group that
    # `Orchestrator._attempt_split` wrote, because on a legacy row that is
    # the only surviving evidence (see ADR 0021, revisión 2026-08-02).
    MigrationStep(
        5,
        "split_depth",
        "INTEGER NOT NULL DEFAULT 0",
        backfill=(
            """
            UPDATE work_items SET split_depth = 1
             WHERE split_depth = 0
               AND (title LIKE '[subtarea] %'
                    OR title LIKE 'Consolidar subtareas: %'
                    OR shared_component LIKE 'split-%')
            """,
        ),
        # Same predicate as the backfill's WHERE clause, as a COUNT: this
        # step is only satisfied once nothing matches it any more. A column
        # added by hand (or left by a pre-P3.2 run that committed the ALTER
        # without its backfill, see ADR 0032) would otherwise read as
        # "satisfied" from column presence alone, permanently skipping rows
        # that still need split_depth = 1.
        postcondition="""
            SELECT COUNT(*) FROM work_items
             WHERE split_depth = 0
               AND (title LIKE '[subtarea] %'
                    OR title LIKE 'Consolidar subtareas: %'
                    OR shared_component LIKE 'split-%')
            """,
    ),
    # Nullable, no backfill: NULL is already correct for every row that
    # predates this column (see ADR 0027 -- nothing constructed a work item
    # with a contract before it existed).
    MigrationStep(6, "execution_contract_json", "JSON"),
    # First step to target a table other than work_items -- see the module
    # docstring. `0 = False` is already correct for every row that predates
    # this column: nothing before P4.1 (not built) can produce a project
    # whose workspace came from an imported repository (P3.4/ADR 0034), so
    # every legacy row backfills to "not imported" with no backfill needed.
    MigrationStep(7, "imported", "BOOLEAN NOT NULL DEFAULT 0", table="projects"),
    # P4.1: display/audit metadata for an imported project. Nullable, no
    # backfill -- nothing before this step (nor any legacy row) can have a
    # real value for either, so NULL is correct throughout.
    MigrationStep(8, "imported_source_path", "TEXT", table="projects"),
    MigrationStep(9, "imported_commit", "VARCHAR(64)", table="projects"),
)

CURRENT_SCHEMA_VERSION = STEPS[-1].version


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def read_user_version(connection: sqlite3.Connection) -> int:
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    return int(version)


def write_user_version(connection: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept bound parameters; `version` is always one of
    # this module's own ints (a STEPS[i].version or CURRENT_SCHEMA_VERSION),
    # never user input.
    connection.execute(f"PRAGMA user_version={int(version)}")


def reject_if_too_new(version: int) -> None:
    """Read-only guard, checked before any mutation (bootstrap included): an
    older code version must never guess at or touch a database a newer one
    already migrated further than this code knows how to."""
    if version > CURRENT_SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"La base de datos está en user_version={version}, más nueva "
            f"que CURRENT_SCHEMA_VERSION={CURRENT_SCHEMA_VERSION} de este "
            "código instalado. Actualizá Agentarium antes de abrir esta "
            "base de datos. No se escribió nada."
        )


def pending_steps(connection: sqlite3.Connection, version: int) -> tuple[MigrationStep, ...]:
    """Steps not yet reflected in the schema.

    When `version` already equals CURRENT_SCHEMA_VERSION this trusts the
    stored number outright -- the hot path for every normal startup: one
    PRAGMA read, no schema inspection. Otherwise -- including the legacy
    `version == 0` case, where the stored number carries no reliable
    information about *which* steps are already applied, only that this
    database predates versioning -- every step is independently re-checked
    against the real schema via `satisfied()`. This is the "bootstrap"
    reconciliation: it doubles as the legacy catch-up scan and as ordinary
    crash recovery, since a database that crashed mid-upgrade is, from this
    function's point of view, indistinguishable from a legacy one -- both
    are "some steps done, version not caught up yet."

    Deliberately does not assume a satisfied *prefix*: a database can carry
    a known column that a step other than the earliest still-missing one
    would add (e.g. a test, or a hand-patched database, that added one
    column out of the usual order). Trusting version number alone for
    "pending" in that case would retry an ALTER TABLE on a column that
    already exists.
    """
    if version >= CURRENT_SCHEMA_VERSION:
        return ()
    return tuple(step for step in STEPS if not step.satisfied(connection))


def apply_step(connection: sqlite3.Connection, step: MigrationStep) -> None:
    """DDL + backfill + the user_version bump all in one explicit
    transaction, so a failure partway through a multi-step upgrade leaves the
    database at exactly the last fully-committed step, never a half-applied
    one.

    Re-checks `satisfied()` itself, even though callers are expected to only
    pass already-pending steps: a cheap, harmless safety net against ever
    re-running an ALTER TABLE on a column that turns out to already exist.
    """
    connection.execute("BEGIN")
    try:
        if not step.satisfied(connection):
            step.apply(connection)
        write_user_version(connection, step.version)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
