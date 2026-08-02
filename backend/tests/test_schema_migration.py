from __future__ import annotations

from pathlib import Path

import pytest
from agentarium.domain.enums import WorkItemStatus
from agentarium.repositories.database import Database
from sqlalchemy import text

# The shape of `work_items` before ADR 0022/0023/0021-revisión added columns:
# no version, no owned_paths_json, no shared_component, no output_strategy and
# no split_depth. Written by hand so the test exercises a real upgrade of a
# database that predates every one of those migrations.
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

_LEGACY_ROW = """
INSERT INTO work_items (
    id, project_id, milestone_id, title, description, assignee_role,
    inputs_json, expected_outputs_json, acceptance_criteria_json,
    allowed_tools_json, authorized_files_json, max_attempts, attempt_count,
    risk, requires_approval, priority, status, created_at, updated_at
) VALUES (
    :id, 'p1', 'm1', :title, 'Descripción', 'implementation_worker',
    '{}', '["resultado"]', '["Primer criterio", "Segundo criterio"]',
    '[]', '[]', 3, 3, 'medium', 0, 50, :status,
    '2026-07-31 00:00:00', '2026-07-31 00:00:00'
)
"""


def _legacy_database(tmp_path: Path) -> Database:
    path = tmp_path / "legacy.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in _LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        rows = [
            ("planned", "Implementar la API"),
            ("child", "[subtarea] Listar libros"),
            ("consolidation", "Consolidar subtareas: Implementar la API"),
        ]
        for identifier, title in rows:
            connection.execute(
                text(_LEGACY_ROW),
                {
                    "id": identifier,
                    "title": title,
                    "status": WorkItemStatus.FAILED.value,
                },
            )
        connection.commit()
    return database


def _depths(database: Database) -> dict[str, int]:
    with database.engine.connect() as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(
                text("SELECT id, split_depth FROM work_items")
            )
        }


def test_migrating_a_legacy_database_backfills_existing_split_lineages(
    tmp_path: Path,
) -> None:
    database = _legacy_database(tmp_path)
    database.create_all()

    depths = _depths(database)
    # A task the planner created may still be split once.
    assert depths["planned"] == 0
    # Everything a previous split produced is already at the limit.
    assert depths["child"] == 1
    assert depths["consolidation"] == 1


def test_the_backfill_reaches_rows_identified_only_by_their_sharing_group(
    tmp_path: Path,
) -> None:
    # A child whose title the model chose freely: the only evidence left on a
    # legacy row is the `split-<parent id>` group _attempt_split wrote.
    path = tmp_path / "legacy.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in _LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        connection.execute(
            text("ALTER TABLE work_items ADD COLUMN shared_component VARCHAR(120)")
        )
        connection.execute(
            text(_LEGACY_ROW),
            {"id": "grouped", "title": "Un titulo cualquiera", "status": "failed"},
        )
        connection.execute(
            text("UPDATE work_items SET shared_component = 'split-abc'")
        )
        connection.commit()

    database.create_all()

    assert _depths(database)["grouped"] == 1


def test_the_migration_is_idempotent(tmp_path: Path) -> None:
    database = _legacy_database(tmp_path)
    database.create_all()
    first = _depths(database)

    # Re-running must not raise and must not move any depth again.
    database.create_all()
    database.create_all()

    assert _depths(database) == first


def test_a_fresh_database_needs_no_backfill(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'fresh.db'}")
    database.create_all()
    database.create_all()

    with database.engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(work_items)"))
        }
    assert "split_depth" in columns


@pytest.mark.parametrize("column", ["version", "owned_paths_json", "output_strategy"])
def test_every_declared_column_exists_after_upgrading_a_legacy_database(
    tmp_path: Path,
    column: str,
) -> None:
    database = _legacy_database(tmp_path)
    database.create_all()

    with database.engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(work_items)"))
        }
    assert column in columns
