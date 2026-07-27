from __future__ import annotations

from .enums import WorkItemStatus


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[WorkItemStatus, frozenset[WorkItemStatus]] = {
    WorkItemStatus.DRAFT: frozenset(
        {WorkItemStatus.BLOCKED, WorkItemStatus.READY, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.BLOCKED: frozenset(
        {WorkItemStatus.READY, WorkItemStatus.AWAITING_APPROVAL, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.READY: frozenset(
        {WorkItemStatus.ASSIGNED, WorkItemStatus.BLOCKED, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.ASSIGNED: frozenset(
        {WorkItemStatus.RUNNING, WorkItemStatus.READY, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.RUNNING: frozenset(
        {
            WorkItemStatus.AWAITING_REVIEW,
            WorkItemStatus.AWAITING_APPROVAL,
            WorkItemStatus.READY,
            WorkItemStatus.FAILED,
            WorkItemStatus.CANCELLED,
        }
    ),
    WorkItemStatus.AWAITING_REVIEW: frozenset(
        {
            WorkItemStatus.CHANGES_REQUESTED,
            WorkItemStatus.PASSED,
            WorkItemStatus.FAILED,
            WorkItemStatus.AWAITING_APPROVAL,
            WorkItemStatus.CANCELLED,
        }
    ),
    WorkItemStatus.CHANGES_REQUESTED: frozenset(
        {WorkItemStatus.READY, WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.AWAITING_APPROVAL: frozenset(
        {WorkItemStatus.READY, WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}
    ),
    WorkItemStatus.PASSED: frozenset({WorkItemStatus.COMPLETED, WorkItemStatus.CHANGES_REQUESTED}),
    WorkItemStatus.FAILED: frozenset({WorkItemStatus.READY, WorkItemStatus.CANCELLED}),
    WorkItemStatus.COMPLETED: frozenset(),
    WorkItemStatus.CANCELLED: frozenset(),
}


def validate_transition(current: WorkItemStatus, target: WorkItemStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"Invalid work item transition: {current.value} -> {target.value}")


def transition(current: WorkItemStatus, target: WorkItemStatus) -> WorkItemStatus:
    validate_transition(current, target)
    return target
