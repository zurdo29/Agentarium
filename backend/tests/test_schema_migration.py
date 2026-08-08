from __future__ import annotations

from pathlib import Path

import pytest
from agentarium.domain.enums import WorkItemStatus
from agentarium.domain.models import Milestone, Project, ScriptExecutionContract, WorkItem
from agentarium.repositories import Repository
from agentarium.repositories.database import Database
from agentarium.repositories.migrations import CURRENT_SCHEMA_VERSION
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

# The other 8 tables this codebase has ever had, at their fd83772 shape --
# confirmed identical to today's `tables.py` (`git show fd83772:...`): only
# `work_items` has ever gained a column. Used by
# test_upgrading_a_full_legacy_schema_reaches_current_version_without_touching_unchanged_tables
# below to prove `create_all()` really does leave all 10 never-changed
# tables alone, not just assume SQLAlchemy's documented create_all()
# semantics without checking.
_LEGACY_OTHER_TABLES = """
CREATE TABLE dependencies (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36) NOT NULL,
    depends_on_id VARCHAR(36) NOT NULL
);
CREATE TABLE agent_runs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36),
    agent_role VARCHAR(50) NOT NULL,
    model VARCHAR(160) NOT NULL,
    provider VARCHAR(80) NOT NULL,
    attempt INTEGER NOT NULL,
    outcome VARCHAR(50) NOT NULL,
    input_summary TEXT NOT NULL,
    output_summary TEXT NOT NULL,
    resource_usage_json JSON NOT NULL,
    correlation_id VARCHAR(36) NOT NULL,
    error TEXT,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NOT NULL
);
CREATE TABLE artifacts (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36) NOT NULL,
    agent_run_id VARCHAR(36) NOT NULL,
    artifact_type VARCHAR(80) NOT NULL,
    title VARCHAR(240) NOT NULL,
    content_json JSON NOT NULL,
    file_paths_json JSON NOT NULL,
    schema_version VARCHAR(20) NOT NULL,
    checksum VARCHAR(128),
    created_at DATETIME NOT NULL
);
CREATE TABLE reviews (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    reviewer_run_id VARCHAR(36) NOT NULL,
    verdict VARCHAR(40) NOT NULL,
    reasons_json JSON NOT NULL,
    acceptance_results_json JSON NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE test_reports (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    tester_run_id VARCHAR(36) NOT NULL,
    passed BOOLEAN NOT NULL,
    checks_json JSON NOT NULL,
    command_evidence_json JSON NOT NULL,
    summary TEXT NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE decisions (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    title VARCHAR(240) NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    reversible BOOLEAN NOT NULL,
    alternatives_json JSON NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE approval_requests (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36),
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk VARCHAR(20) NOT NULL,
    alternatives_json JSON NOT NULL,
    affected_resources_json JSON NOT NULL,
    status VARCHAR(20) NOT NULL,
    comments TEXT,
    created_at DATETIME NOT NULL,
    resolved_at DATETIME
);
CREATE TABLE execution_events (
    sequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    id VARCHAR(36) NOT NULL UNIQUE,
    project_id VARCHAR(36) NOT NULL,
    work_item_id VARCHAR(36),
    agent_run_id VARCHAR(36),
    agent_role VARCHAR(50),
    model VARCHAR(160),
    action VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    previous_state VARCHAR(40),
    new_state VARCHAR(40),
    attempt INTEGER,
    error TEXT,
    resource_usage_json JSON,
    correlation_id VARCHAR(36) NOT NULL,
    metadata_json JSON NOT NULL,
    timestamp DATETIME NOT NULL
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


@pytest.mark.parametrize(
    "column",
    ["version", "owned_paths_json", "output_strategy", "execution_contract_json"],
)
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


def test_projects_gains_imported_column_after_upgrading_a_legacy_database(
    tmp_path: Path,
) -> None:
    """P3.4 (ADR 0034): first migration step to ever target `projects`
    instead of `work_items` -- proves `MigrationStep.table` actually reaches
    a different table, not just that `work_items` steps still work."""
    database = _legacy_database(tmp_path)
    with database.engine.connect() as connection:
        # A pre-existing project row, in the legacy shape (no `imported`
        # column) -- `_legacy_database()` only creates the table, it never
        # inserts into it.
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, title, goal, status, progress_percent, created_at, updated_at) "
                "VALUES ('p1', 'Legacy', 'Legacy goal', 'draft', 0, "
                "'2026-07-31 00:00:00', '2026-07-31 00:00:00')"
            )
        )
        connection.commit()

    database.create_all()

    with database.engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(projects)"))
        }
        assert "imported" in columns
        (imported,) = connection.execute(
            text("SELECT imported FROM projects WHERE id = 'p1'")
        ).fetchone()
    assert imported == 0
    database.dispose()


def test_a_project_created_with_imported_true_round_trips(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}")
    database.create_all()
    repository = Repository(database)

    project = repository.create_project(
        Project(title="Imported project", goal="Repo importado", imported=True)
    )
    reloaded = repository.get_project(project.id)

    assert reloaded.imported is True
    database.dispose()


def _project_with_milestone(repository: Repository) -> Milestone:
    project = repository.create_project(
        Project(title="Schema migration test", goal="Round-trip a work item")
    )
    milestone = Milestone(
        project_id=project.id,
        title="Milestone",
        description="Milestone",
        order=0,
    )
    repository.add_milestone(milestone)
    return milestone


def test_a_work_item_execution_contract_round_trips(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}")
    database.create_all()
    repository = Repository(database)
    milestone = _project_with_milestone(repository)
    item = WorkItem(
        project_id=milestone.project_id,
        milestone_id=milestone.id,
        title="task",
        description="description",
        expected_outputs=["tool.py"],
        acceptance_criteria=["ejecuta correctamente"],
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", args=["input.csv"], produces="result.json"
        ),
    )

    repository.add_work_item(item)
    reloaded = repository.get_work_item(item.id)

    assert reloaded.execution_contract is not None
    assert reloaded.execution_contract.entrypoint == "tool.py"
    assert reloaded.execution_contract.args == ["input.csv"]
    assert reloaded.execution_contract.produces == "result.json"
    database.dispose()


def test_a_work_item_without_a_contract_loads_execution_contract_as_none(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}")
    database.create_all()
    repository = Repository(database)
    milestone = _project_with_milestone(repository)
    item = WorkItem(
        project_id=milestone.project_id,
        milestone_id=milestone.id,
        title="task",
        description="description",
        expected_outputs=["artifact"],
        acceptance_criteria=["passes"],
    )

    repository.add_work_item(item)
    reloaded = repository.get_work_item(item.id)

    assert reloaded.execution_contract is None
    database.dispose()


# The 9 tables besides work_items and projects -- see _LEGACY_OTHER_TABLES
# above. `projects` moved out of this tuple in P3.4/ADR 0034, the first
# migration step to target it (see
# test_projects_gains_imported_column_after_upgrading_a_legacy_database).
_NEVER_CHANGED_TABLES = (
    "milestones",
    "dependencies",
    "agent_runs",
    "artifacts",
    "reviews",
    "test_reports",
    "decisions",
    "approval_requests",
    "execution_events",
)


def _table_info(database: Database, table: str) -> list[tuple[object, ...]]:
    with database.engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(f"PRAGMA table_info({table})"))]


def test_upgrading_a_full_legacy_schema_reaches_current_version_without_touching_unchanged_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-full.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in (_LEGACY_SCHEMA + _LEGACY_OTHER_TABLES).strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        connection.commit()

    before = {table: _table_info(database, table) for table in _NEVER_CHANGED_TABLES}

    database.create_all()

    with database.engine.connect() as connection:
        (version,) = connection.execute(text("PRAGMA user_version")).fetchone()
    assert version == CURRENT_SCHEMA_VERSION

    after = {table: _table_info(database, table) for table in _NEVER_CHANGED_TABLES}
    assert after == before

    with database.engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(work_items)"))
        }
    assert {
        "version",
        "owned_paths_json",
        "shared_component",
        "output_strategy",
        "split_depth",
        "execution_contract_json",
    } <= columns
    database.dispose()


def test_bootstrap_still_backfills_split_depth_when_the_column_exists_without_its_data(
    tmp_path: Path,
) -> None:
    """satisfied() must check the *data* postcondition a backfill step
    promises, not just that its column exists. A column added by hand (or
    left behind by a pre-P3.2 run whose ALTER committed without its
    UPDATE -- see ADR 0032 on pysqlite's non-transactional DDL) must still
    get backfilled, and apply() must not try to re-ALTER a column that's
    already there."""
    path = tmp_path / "legacy.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in _LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        # shared_component and split_depth both exist, but split_depth was
        # never backfilled -- every row, including ones the backfill's own
        # title/shared_component patterns match, is still at its DEFAULT 0.
        connection.execute(
            text("ALTER TABLE work_items ADD COLUMN shared_component VARCHAR(120)")
        )
        connection.execute(
            text("ALTER TABLE work_items ADD COLUMN split_depth INTEGER NOT NULL DEFAULT 0")
        )
        connection.execute(
            text(_LEGACY_ROW),
            {
                "id": "child",
                "title": "[subtarea] Listar libros",
                "status": WorkItemStatus.FAILED.value,
            },
        )
        connection.commit()

    database.create_all()

    assert _depths(database)["child"] == 1


def test_out_of_order_legacy_steps_still_reach_current_version(
    tmp_path: Path,
) -> None:
    """pending_steps() must not assume version implies a satisfied prefix in
    either direction: a database can have a *later* step's column already
    present while an *earlier* one is still missing (e.g. a hand-patched
    database). The upgrade must still apply the missing step and land
    exactly on CURRENT_SCHEMA_VERSION, not stall at whatever step happened
    to be the last one actually applied."""
    path = tmp_path / "legacy.db"
    database = Database(f"sqlite:///{path}")
    with database.engine.connect() as connection:
        for statement in _LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
        # step 6 (execution_contract_json) present; step 5 (split_depth) and
        # everything before it deliberately absent.
        connection.execute(
            text("ALTER TABLE work_items ADD COLUMN execution_contract_json JSON")
        )
        connection.commit()

    database.create_all()

    with database.engine.connect() as connection:
        (version,) = connection.execute(text("PRAGMA user_version")).fetchone()
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(work_items)"))
        }
    assert version == CURRENT_SCHEMA_VERSION
    assert {
        "version",
        "owned_paths_json",
        "shared_component",
        "output_strategy",
        "split_depth",
        "execution_contract_json",
    } <= columns
    database.dispose()
