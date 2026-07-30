import pytest
from agentarium.domain.enums import ProjectStatus, ReviewVerdict, WorkItemStatus
from agentarium.orchestration.engine import DEPENDENCY_CONSISTENCY_CRITERION
from agentarium.services import ApplicationService


@pytest.mark.asyncio
async def test_complete_vertical_flow_with_mock_correction(
    service: ApplicationService,
) -> None:
    project = service.create_project(
        "Crear un pequeño ARPG con progresión de objetos y alcance de prototipo"
    )
    completed = await service.run_project(project.id)

    assert completed.status is ProjectStatus.COMPLETED
    detail = service.project_detail(project.id)
    assert detail["project"]["brief"] is not None
    assert len(detail["work_items"]) == 3
    assert all(item["status"] == WorkItemStatus.COMPLETED.value for item in detail["work_items"])

    corrected = next(
        item for item in detail["work_items"] if item["title"] == "Producir el artefacto principal"
    )
    assert corrected["attempt_count"] == 2
    assert len(detail["artifacts"]) == 4
    assert sorted(len(artifact["file_paths"]) for artifact in detail["artifacts"]) == [
        1,
        2,
        2,
        2,
    ]
    product_root = service.settings.workspace_root / project.id / "project"
    assert (product_root / ".git").is_dir()
    assert (product_root / "docs" / "specification.md").is_file()
    assert (product_root / "src" / "implementation.md").is_file()
    assert (product_root / "README.md").is_file()
    assert project.goal in (product_root / "README.md").read_text(encoding="utf-8")
    assert all(
        any(
            evidence["check"] == "workspace_file_checksum" and evidence["verified"]
            for evidence in report["command_evidence"]
        )
        for report in detail["test_reports"]
    )
    assert all(
        any(
            evidence["check"] == "validation_profile"
            and evidence["profile"] == "workspace_inventory"
            and evidence["passed"]
            and evidence["return_code"] == 0
            for evidence in report["command_evidence"]
        )
        for report in detail["test_reports"]
    )
    assert all(
        any(
            evidence["check"] == "isolated_change_set"
            and evidence["backend"] == "git_worktree"
            and evidence["verified"]
            for evidence in report["command_evidence"]
        )
        for report in detail["test_reports"]
    )
    worktrees_root = service.settings.workspace_root / project.id / "worktrees"
    assert not any(worktrees_root.rglob(".git"))
    assert any(
        review["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value for review in detail["reviews"]
    )
    assert all(report["passed"] for report in detail["test_reports"])
    assert detail["metrics"]["tasks_completed"] == 3
    assert detail["metrics"]["tasks_rejected"] == 1
    assert detail["metrics"]["retries"] == 1

    event_actions = {
        event["action"] for event in service.repository.list_events(project.id, limit=1000)
    }
    assert {
        "project_created",
        "planning_completed",
        "workspace_files_materialized",
        "validation_profiles_completed",
        "change_set_integrated",
        "review_rejected",
        "project_completed",
    }.issubset(event_actions)


@pytest.mark.asyncio
async def test_completed_work_is_persisted_and_not_repeated(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.run_project(project.id)
    attempts_before = sum(
        item.attempt_count for item in service.repository.list_work_items(project.id)
    )

    second_result = await service.run_project(project.id)
    attempts_after = sum(
        item.attempt_count for item in service.repository.list_work_items(project.id)
    )

    assert second_result.status is ProjectStatus.COMPLETED
    assert attempts_after == attempts_before


@pytest.mark.asyncio
async def test_dependency_consistency_criterion_only_applies_when_relevant(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.run_project(project.id)

    items = {
        item.id: item
        for item in service.repository.list_work_items(project.id)
    }
    reviews = service.repository.list_reviews(project.id)
    with_dependencies = [
        review for review in reviews if items[review.work_item_id].dependency_ids
    ]
    without_dependencies = [
        review
        for review in reviews
        if not items[review.work_item_id].dependency_ids
    ]

    assert with_dependencies
    assert without_dependencies
    assert all(
        DEPENDENCY_CONSISTENCY_CRITERION in review.acceptance_results
        for review in with_dependencies
    )
    assert all(
        DEPENDENCY_CONSISTENCY_CRITERION not in review.acceptance_results
        for review in without_dependencies
    )
