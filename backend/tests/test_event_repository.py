from __future__ import annotations

from pathlib import Path

from agentarium.domain.models import ExecutionEvent, Project
from agentarium.repositories import Database, Repository


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}")
    database.create_all()
    return database


def test_list_events_for_work_item_filters_by_work_item(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Filtro de eventos", goal="Aislar eventos por work item")
    repository.create_project(project)

    other_item_id = "other-item"
    target_item_id = "target-item"
    repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=target_item_id,
            action="unsupported_capability_detected",
            message="primero",
        )
    )
    repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=other_item_id,
            action="unsupported_capability_detected",
            message="de otra tarea",
        )
    )
    repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=None,
            action="planning_failed",
            message="sin work item",
        )
    )
    repository.add_event(
        ExecutionEvent(
            project_id=project.id,
            work_item_id=target_item_id,
            action="unsupported_capability_detected",
            message="segundo",
        )
    )

    events = repository.list_events_for_work_item(project.id, target_item_id)

    assert [event["message"] for event in events] == ["primero", "segundo"]
    database.dispose()


def test_list_events_for_work_item_keeps_the_most_recent_within_limit(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = Repository(database)
    project = Project(title="Limite de eventos", goal="Conservar los mas recientes")
    repository.create_project(project)
    item_id = "target-item"

    for index in range(5):
        repository.add_event(
            ExecutionEvent(
                project_id=project.id,
                work_item_id=item_id,
                action="unsupported_capability_detected",
                message=f"evento-{index}",
            )
        )

    events = repository.list_events_for_work_item(project.id, item_id, limit=2)

    assert [event["message"] for event in events] == ["evento-3", "evento-4"]
    database.dispose()
