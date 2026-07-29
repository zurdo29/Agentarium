import pytest
from agentarium.domain.models import ProjectBrief, WorkItem
from pydantic import ValidationError


def test_brief_requires_success_criteria() -> None:
    with pytest.raises(ValidationError):
        ProjectBrief(
            project_id="project",
            summary="summary",
            scope=["scope"],
            deliverables=["deliverable"],
        )


def test_work_item_rejects_zero_attempt_budget() -> None:
    with pytest.raises(ValidationError):
        WorkItem(
            project_id="project",
            milestone_id="milestone",
            title="task",
            description="description",
            expected_outputs=["artifact"],
            acceptance_criteria=["passes"],
            max_attempts=0,
        )


def test_work_item_allows_audited_recovery_beyond_ten_attempts() -> None:
    work_item = WorkItem(
        project_id="project",
        milestone_id="milestone",
        title="task",
        description="description",
        expected_outputs=["artifact"],
        acceptance_criteria=["passes"],
        max_attempts=11,
    )

    assert work_item.max_attempts == 11


def test_work_item_retains_a_finite_attempt_ceiling() -> None:
    with pytest.raises(ValidationError):
        WorkItem(
            project_id="project",
            milestone_id="milestone",
            title="task",
            description="description",
            expected_outputs=["artifact"],
            acceptance_criteria=["passes"],
            max_attempts=26,
        )
