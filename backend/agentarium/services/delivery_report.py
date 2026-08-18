"""Delivery report built from data already persisted elsewhere -- same
`build_payload`-only, no-I/O shape as `export_summary.py`'s
`build_export_summary`. Unlike that module, this one is not scoped to a
chosen git export range: "qué cambió" here is the project's cumulative
state at any point in its life, so it reads real `change_set_integrated`
events for genuine integration facts but never touches git or
`isolation` directly -- a project that has never been exported still
gets a real report.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentarium.domain.enums import VerificationMode, WorkItemStatus
from agentarium.domain.models import Project, Review, TestReport, WorkItem


def build_delivery_report(
    *,
    project: Project,
    work_items: list[WorkItem],
    reviews: list[Review],
    test_reports: list[TestReport],
    stuck_by_work_item: dict[str, tuple[str, WorkItem | None]],
    awaiting_approval_work_item_ids: set[str],
    integration_events: list[dict[str, Any]],
    split_parent_work_item_ids: set[str],
) -> dict[str, Any]:
    # Same "last one wins" idiom as export_summary.py:33-34.
    latest_review_by_work_item = {review.work_item_id: review for review in reviews}
    latest_test_report_by_work_item = {report.work_item_id: report for report in test_reports}
    integration_by_work_item = {
        str(event.get("work_item_id") or ""): event.get("metadata") or {}
        for event in integration_events
    }

    items_payload: list[dict[str, Any]] = []
    # Two distinct reasons an item lands here, never both at once for the
    # same item (Gate-MVP.2, ADR 0041):
    # - "missing_review_or_test_report": data-integrity safety net for a
    #   hand-edited DB (SQLite can be edited directly) -- under every
    #   normal code path a COMPLETED item already required a passing
    #   test_report and an APPROVED review (engine.py's single gate into
    #   integrate()), so this case is expected to be empty in normal
    #   operation.
    # - "static_only_verification": the *expected*, common case for an
    #   imported project -- COMPLETED can legitimately mean "the workflow
    #   finished", but must never silently read as "this was proven to
    #   run". `verification_mode` is what actually distinguishes the two.
    unverified_completed_items: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()

    for item in work_items:
        review = latest_review_by_work_item.get(item.id)
        test_report = latest_test_report_by_work_item.get(item.id)
        integration = integration_by_work_item.get(item.id)
        stuck = stuck_by_work_item.get(item.id)

        # Priority: terminal facts first (a stray pending approval or
        # stuck-classification must never override a hard COMPLETED/
        # CANCELLED outcome), then an active pending approval (derived
        # only from a real task_escalated event whose metadata names a
        # real pending approval for this exact item -- item.status is
        # never AWAITING_APPROVAL in any code path today, and a generic
        # approval's own work_item_id alone is not enough, same
        # structural-link discipline as resolve_approval), then stuck,
        # else still moving.
        if item.status is WorkItemStatus.COMPLETED:
            outcome = "completed"
        elif item.status is WorkItemStatus.CANCELLED:
            outcome = (
                "superseded_by_split"
                if item.id in split_parent_work_item_ids
                else "cancelled"
            )
        elif item.id in awaiting_approval_work_item_ids:
            outcome = "awaiting_approval"
        elif stuck is not None:
            outcome = stuck[0]
        else:
            outcome = "in_progress"

        totals[outcome] += 1

        if outcome == "completed":
            if review is None or test_report is None:
                unverified_completed_items.append(
                    {
                        "work_item_id": item.id,
                        "title": item.title,
                        "reason": "missing_review_or_test_report",
                    }
                )
            elif test_report.verification_mode is VerificationMode.STATIC_ONLY:
                unverified_completed_items.append(
                    {
                        "work_item_id": item.id,
                        "title": item.title,
                        "reason": "static_only_verification",
                    }
                )

        blocking = stuck[1] if stuck is not None else None
        items_payload.append(
            {
                "work_item_id": item.id,
                "title": item.title,
                "status": item.status.value,
                "outcome": outcome,
                "attempt_count": item.attempt_count,
                "max_attempts": item.max_attempts,
                "review_verdict": review.verdict.value if review else None,
                "review_reasons": review.reasons if review else [],
                "review_acceptance_results": review.acceptance_results if review else {},
                "test_passed": test_report.passed if test_report else None,
                "test_verification_mode": (
                    test_report.verification_mode.value if test_report else None
                ),
                "test_summary": test_report.summary if test_report else None,
                "test_checks": test_report.checks if test_report else [],
                "test_command_evidence": test_report.command_evidence if test_report else [],
                "integration_commit": (
                    integration.get("integration_commit") if integration else None
                ),
                "integration_branch": integration.get("branch") if integration else None,
                "integration_files": integration.get("files", []) if integration else [],
                "blocking_dependency_id": blocking.id if blocking else None,
                "blocking_dependency_title": blocking.title if blocking else None,
            }
        )

    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "goal": project.goal,
            "brief": project.brief.model_dump(mode="json") if project.brief else None,
        },
        "work_items": items_payload,
        "unverified_completed_items": unverified_completed_items,
        "totals": dict(totals),
    }
