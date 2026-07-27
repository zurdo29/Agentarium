import pytest
from agentarium.domain.enums import WorkItemStatus
from agentarium.domain.state_machine import InvalidTransition, transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkItemStatus.DRAFT, WorkItemStatus.READY),
        (WorkItemStatus.READY, WorkItemStatus.ASSIGNED),
        (WorkItemStatus.ASSIGNED, WorkItemStatus.RUNNING),
        (WorkItemStatus.RUNNING, WorkItemStatus.AWAITING_REVIEW),
        (WorkItemStatus.AWAITING_REVIEW, WorkItemStatus.PASSED),
        (WorkItemStatus.PASSED, WorkItemStatus.COMPLETED),
        (WorkItemStatus.CHANGES_REQUESTED, WorkItemStatus.READY),
        (WorkItemStatus.FAILED, WorkItemStatus.READY),
    ],
)
def test_valid_transitions(current: WorkItemStatus, target: WorkItemStatus) -> None:
    assert transition(current, target) is target


def test_terminal_state_cannot_transition() -> None:
    with pytest.raises(InvalidTransition):
        transition(WorkItemStatus.COMPLETED, WorkItemStatus.READY)


def test_impossible_shortcut_is_rejected() -> None:
    with pytest.raises(InvalidTransition):
        transition(WorkItemStatus.DRAFT, WorkItemStatus.COMPLETED)
