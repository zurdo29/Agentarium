"""One place that decides why a benchmark run ended the way it did.

Every consumer — report, ledger, future dashboards — reads categories from
here. The set is fixed on purpose: a taxonomy that grows a bucket per surprise
stops being comparable across runs.

This module only *reads* persisted state. It never changes execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agentarium.domain.enums import ProjectStatus, WorkItemStatus
from agentarium.domain.models import Project, WorkItem


class FailureCategory(StrEnum):
    COMPLETED = "completed"
    PLANNING_CONTRACT = "planning_contract"
    PATH_CONFLICT = "path_conflict"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    TECHNICAL_VALIDATION = "technical_validation"
    SEMANTIC_REJECTION = "semantic_rejection"
    PROVIDER_FAILURE = "provider_failure"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True)
class Classification:
    category: FailureCategory
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {"category": self.category.value, "evidence": self.evidence}


# Signatures are matched against the error text a task ended with. They are
# ordered: the first match wins, so the more specific ones come first.
_ERROR_SIGNATURES: tuple[tuple[str, FailureCategory], ...] = (
    ("byte-for-byte identical", FailureCategory.DUPLICATE_CANDIDATE),
    ("collide with files already owned", FailureCategory.PATH_CONFLICT),
    ("modulenotfounderror", FailureCategory.UNSUPPORTED_CAPABILITY),
    ("no module named", FailureCategory.UNSUPPORTED_CAPABILITY),
    ("importerror", FailureCategory.UNSUPPORTED_CAPABILITY),
    ("could not be written", FailureCategory.INFRASTRUCTURE),
    ("could not be read back", FailureCategory.INFRASTRUCTURE),
    ("could not be installed", FailureCategory.INFRASTRUCTURE),
    ("escaped", FailureCategory.INFRASTRUCTURE),
    ("git ", FailureCategory.INFRASTRUCTURE),
    ("invalid workspace artifact", FailureCategory.TECHNICAL_VALIDATION),
    ("workspace file paths must be", FailureCategory.TECHNICAL_VALIDATION),
)


def classify(
    project: Project,
    items: list[WorkItem],
    events: list[dict[str, Any]],
    *,
    validation_passed: bool,
) -> Classification:
    """Why this run ended as it did.

    `validation_passed` comes from the case's own validators, which read the
    integrated files instead of trusting the agent's summary. A project the
    orchestrator called `completed` but whose artifacts do not satisfy the case
    is not `completed` here — that is the false-completion the benchmark exists
    to count.
    """
    actions = [str(event.get("action", "")) for event in events]

    if project.status is ProjectStatus.COMPLETED and validation_passed:
        return Classification(FailureCategory.COMPLETED, "validadores independientes en verde")

    if project.status is ProjectStatus.COMPLETED and not validation_passed:
        return Classification(
            FailureCategory.TECHNICAL_VALIDATION,
            "el proyecto se declaró completo pero los validadores del caso fallaron",
        )

    if "planning_failed" in actions:
        return Classification(
            FailureCategory.PLANNING_CONTRACT,
            "la planificación no produjo un plan utilizable",
        )

    if "agent_run_failed" in actions:
        return Classification(
            FailureCategory.PROVIDER_FAILURE,
            _first_message(events, "agent_run_failed"),
        )

    failed = [item for item in items if item.status is WorkItemStatus.FAILED]
    for item in failed:
        matched = _match_error(item.last_error)
        if matched is not None:
            return Classification(matched, f"{item.title}: {item.last_error}")

    if any(action == "review_rejected" for action in actions):
        return Classification(
            FailureCategory.SEMANTIC_REJECTION,
            _first_message(events, "review_rejected"),
        )

    if failed:
        return Classification(
            FailureCategory.TECHNICAL_VALIDATION,
            f"{failed[0].title}: {failed[0].last_error}",
        )

    if "plan_owned_path_conflict_unresolved" in actions:
        return Classification(
            FailureCategory.PATH_CONFLICT,
            "quedó un conflicto de propiedad sin resolver en el plan",
        )

    return Classification(
        FailureCategory.PLANNING_CONTRACT,
        f"el proyecto terminó en {project.status.value} sin una tarea fallida",
    )


def _match_error(error: str | None) -> FailureCategory | None:
    if not error:
        return None
    lowered = error.casefold()
    for signature, category in _ERROR_SIGNATURES:
        if signature in lowered:
            return category
    return None


def _first_message(events: list[dict[str, Any]], action: str) -> str:
    for event in events:
        if event.get("action") == action:
            return str(event.get("message") or event.get("error") or action)
    return action
