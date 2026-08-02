"""One place that decides why a benchmark run ended the way it did.

Every consumer — report, ledger, future dashboards — reads categories from
here. The set is fixed on purpose: a taxonomy that grows a bucket per surprise
stops being comparable across runs.

The rule throughout is **terminal cause**. An incident earlier in the run that
the orchestrator recovered from is not why the run ended, and recording it as
such would hide the real cause. A provider that times out once, retries, and
then dies on a duplicate candidate is a `duplicate_candidate` run.

This module only *reads* persisted state. It never changes execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agentarium.domain.enums import ProjectStatus, ReviewVerdict, WorkItemStatus
from agentarium.domain.models import Project, Review, TestReport, WorkItem


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


# Matched against the error a task ended with. Ordered: first match wins, so
# the more specific signatures come first.
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
    reviews: list[Review] | None = None,
    test_reports: list[TestReport] | None = None,
) -> Classification:
    """Why this run ended as it did.

    `validation_passed` comes from the case's own validators, which read the
    integrated files instead of trusting the agent's summary. A project the
    orchestrator called `completed` but whose artifacts do not satisfy the case
    is not `completed` here — that is the false completion the benchmark exists
    to count.
    """
    if project.status is ProjectStatus.COMPLETED:
        if validation_passed:
            return Classification(
                FailureCategory.COMPLETED,
                "validadores independientes en verde",
            )
        return Classification(
            FailureCategory.TECHNICAL_VALIDATION,
            "el proyecto se declaró completo pero los validadores del caso fallaron",
        )

    terminal = _terminal_failed_item(items)
    if terminal is not None:
        return _classify_item(terminal, events, reviews or [], test_reports or [])

    if any(event.get("action") == "planning_failed" for event in events):
        # A provider that died during planning is a provider failure, not a
        # planner that produced a bad contract.
        if _provider_failed_planning(events):
            return Classification(
                FailureCategory.PROVIDER_FAILURE,
                _message(events, "planning_failed"),
            )
        return Classification(
            FailureCategory.PLANNING_CONTRACT,
            _message(events, "planning_failed"),
        )

    if any(
        event.get("action") == "plan_owned_path_conflict_unresolved" for event in events
    ):
        return Classification(
            FailureCategory.PATH_CONFLICT,
            "quedó un conflicto de propiedad sin resolver en el plan",
        )

    return Classification(
        FailureCategory.PLANNING_CONTRACT,
        f"el proyecto terminó en {project.status.value} sin una tarea fallida",
    )


def _terminal_failed_item(items: list[WorkItem]) -> WorkItem | None:
    """The failed task that ended last: the one the run actually died on."""
    failed = [item for item in items if item.status is WorkItemStatus.FAILED]
    if not failed:
        return None
    return max(failed, key=lambda item: item.updated_at)


def _classify_item(
    item: WorkItem,
    events: list[dict[str, Any]],
    reviews: list[Review],
    test_reports: list[TestReport],
) -> Classification:
    label = f"{item.title}: {item.last_error}"

    # Structural, not textual: a provider failure is terminal for this task
    # only if it happened on the attempt the task died on. An earlier one was
    # recovered from and is not why the run ended.
    if _provider_failed_on_final_attempt(item, events):
        return Classification(FailureCategory.PROVIDER_FAILURE, label)

    matched = _match_error(item.last_error)
    if matched is not None:
        return Classification(matched, label)

    # Technical first, and the order is load-bearing:
    # `_apply_technical_review_gate` forces the review to CHANGES_REQUESTED
    # whenever the technical gate failed, so a red report always comes with a
    # rejected review. Reading the review first would file every technical
    # failure as a semantic one.
    last_report = _last_for_item(test_reports, item.id)
    if last_report is not None and not last_report.passed:
        return Classification(
            FailureCategory.TECHNICAL_VALIDATION,
            f"{item.title}: {last_report.summary}",
        )

    # Only a rejection the technical gate did not cause is semantic.
    last_review = _last_for_item(reviews, item.id)
    if last_review is not None and last_review.verdict is not ReviewVerdict.APPROVED:
        reasons = "; ".join(last_review.reasons) or "sin motivo declarado"
        return Classification(
            FailureCategory.SEMANTIC_REJECTION,
            f"{item.title}: {reasons}",
        )

    return Classification(FailureCategory.TECHNICAL_VALIDATION, label)


def _provider_failed_on_final_attempt(
    item: WorkItem,
    events: list[dict[str, Any]],
) -> bool:
    return any(
        event.get("action") == "agent_run_failed"
        and event.get("work_item_id") == item.id
        and event.get("attempt") == item.attempt_count
        for event in events
    )


def _provider_failed_planning(events: list[dict[str, Any]]) -> bool:
    return any(
        event.get("action") == "agent_run_failed" and not event.get("work_item_id")
        for event in events
    )


def _last_for_item(records: list[Any], work_item_id: str) -> Any | None:
    matching = [record for record in records if record.work_item_id == work_item_id]
    return matching[-1] if matching else None


def _match_error(error: str | None) -> FailureCategory | None:
    if not error:
        return None
    lowered = error.casefold()
    for signature, category in _ERROR_SIGNATURES:
        if signature in lowered:
            return category
    return None


def _message(events: list[dict[str, Any]], action: str) -> str:
    for event in events:
        if event.get("action") == action:
            return str(event.get("error") or event.get("message") or action)
    return action
