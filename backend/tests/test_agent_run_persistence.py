from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentarium.domain.enums import AgentRole, RunOutcome
from agentarium.domain.models import AgentRun, Project, ResourceUsage
from agentarium.repositories import Database, Repository
from agentarium.repositories.tables import AgentRunRow


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}")
    database.create_all()
    return database


def _agent_run(project_id: str, resource_usage: ResourceUsage) -> AgentRun:
    now = datetime.now(UTC)
    return AgentRun(
        project_id=project_id,
        work_item_id=None,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="fake-model",
        provider="fake",
        attempt=1,
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="{}",
        output_summary="done",
        resource_usage=resource_usage,
        correlation_id="corr-1",
        started_at=now,
        finished_at=now,
    )


def test_agent_run_round_trip_persists_new_timing_fields(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Persistence test", goal="Round-trip a resource usage")
    repository.create_project(project)
    run = _agent_run(
        project.id,
        ResourceUsage(
            duration_ms=500,
            queue_wait_ms=320,
            generation_ms=180,
            model="fake-model",
            provider="fake",
        ),
    )

    repository.add_agent_run(run)

    with database.session() as session:
        row = session.get(AgentRunRow, run.id)
        assert row is not None
        reloaded = ResourceUsage.model_validate(row.resource_usage_json)

    assert reloaded.queue_wait_ms == 320
    assert reloaded.generation_ms == 180
    database.dispose()


def test_legacy_resource_usage_json_without_new_fields_reads_as_none(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Persistence test", goal="Read back a legacy record")
    repository.create_project(project)
    run = _agent_run(
        project.id, ResourceUsage(duration_ms=100, model="fake-model", provider="fake")
    )
    repository.add_agent_run(run)

    # No migration ever ran for this column (it is a JSON blob, not fixed
    # columns): simulate a row written before queue_wait_ms/generation_ms
    # existed by overwriting it with a payload that never had those keys.
    legacy_payload = {
        "duration_ms": 100,
        "prompt_characters": 0,
        "response_characters": 0,
        "prompt_tokens_approx": 0,
        "response_tokens_approx": 0,
        "model": "fake-model",
        "provider": "fake",
        "errors": 0,
    }
    with database.session() as session:
        row = session.get(AgentRunRow, run.id)
        assert row is not None
        row.resource_usage_json = legacy_payload

    with database.session() as session:
        row = session.get(AgentRunRow, run.id)
        assert row is not None
        reloaded = ResourceUsage.model_validate(row.resource_usage_json)

    assert reloaded.queue_wait_ms is None
    assert reloaded.generation_ms is None
    database.dispose()


# -- P4.4a: list_agent_runs() -- the one gap this file existed to close
# (add_agent_run had no paired list_ method at all before this phase).


def test_list_agent_runs_round_trips_every_field(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Listado de corridas", goal="Round-trip completo")
    repository.create_project(project)
    run = _agent_run(
        project.id,
        ResourceUsage(
            duration_ms=500,
            queue_wait_ms=320,
            generation_ms=180,
            errors=2,
            model="fake-model",
            provider="fake",
        ),
    )
    repository.add_agent_run(run)

    [reloaded] = repository.list_agent_runs(project.id)

    assert reloaded.id == run.id
    assert reloaded.work_item_id is None
    assert reloaded.agent_role == run.agent_role
    assert reloaded.model == "fake-model"
    assert reloaded.provider == "fake"
    assert reloaded.outcome == run.outcome
    assert reloaded.correlation_id == "corr-1"
    # Not stripped or flattened -- P4.4b's frontend type must not trim
    # this either (see plan correction 4).
    assert reloaded.resource_usage.duration_ms == 500
    assert reloaded.resource_usage.queue_wait_ms == 320
    assert reloaded.resource_usage.generation_ms == 180
    assert reloaded.resource_usage.errors == 2
    database.dispose()


def test_list_agent_runs_is_scoped_to_its_own_project(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    first = Project(title="Primer proyecto", goal="Con su propia corrida")
    second = Project(title="Segundo proyecto", goal="Con la suya")
    repository.create_project(first)
    repository.create_project(second)
    run_a = _agent_run(first.id, ResourceUsage(duration_ms=1, model="m", provider="p"))
    run_b = _agent_run(second.id, ResourceUsage(duration_ms=1, model="m", provider="p"))
    repository.add_agent_run(run_a)
    repository.add_agent_run(run_b)

    assert [run.id for run in repository.list_agent_runs(first.id)] == [run_a.id]
    assert [run.id for run in repository.list_agent_runs(second.id)] == [run_b.id]
    database.dispose()


def test_list_agent_runs_orders_by_started_at_then_id_as_a_stable_tiebreak(
    tmp_path: Path,
) -> None:
    """Correction 4 on the P4.4 plan: the mock provider can produce
    several runs with an identical millisecond started_at -- a plain
    single-column order_by (list_reviews/list_test_reports's own idiom)
    would leave ties in whatever order SQLite happens to return them,
    not reproducible across runs. id is an explicit, deterministic
    secondary key."""
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Corridas simultaneas", goal="Mismo timestamp")
    repository.create_project(project)
    tied_at = datetime(2026, 1, 1, tzinfo=UTC)
    first = _agent_run(project.id, ResourceUsage(duration_ms=1, model="m", provider="p"))
    first = first.model_copy(update={"started_at": tied_at, "finished_at": tied_at})
    second = _agent_run(project.id, ResourceUsage(duration_ms=1, model="m", provider="p"))
    second = second.model_copy(update={"started_at": tied_at, "finished_at": tied_at})
    ordered_ids = sorted([first.id, second.id])
    repository.add_agent_run(first)
    repository.add_agent_run(second)

    result = repository.list_agent_runs(project.id)

    assert [run.id for run in result] == ordered_ids
    database.dispose()
