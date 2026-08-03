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
