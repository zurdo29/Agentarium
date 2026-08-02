"""What the technical and semantic gates actually said, read from what they wrote.

`TestReport` and `Review` are the persisted output of the two gates. Deriving
these from project status instead would conflate them: a task can pass the
technical gate and still be rejected by the reviewer, and a benchmark that
reports both as `false` cannot tell those two failures apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentarium.domain.enums import ReviewVerdict, WorkItemStatus
from agentarium.domain.models import Review, TestReport, WorkItem


@dataclass(frozen=True)
class GateResults:
    technical: bool
    semantic: bool
    technical_reports: int
    semantic_reviews: int


def gate_results(
    items: list[WorkItem],
    reviews: list[Review],
    test_reports: list[TestReport],
) -> GateResults:
    """Last verdict per task wins; tasks cancelled by a split are excluded.

    A parent cancelled by `_attempt_split` was superseded, never delivered, and
    its last rejection describes work that no longer exists — counting it would
    report a failure the run already answered by decomposing.
    """
    considered = {
        item.id
        for item in items
        if item.status is not WorkItemStatus.CANCELLED
    }

    last_report: dict[str, TestReport] = {}
    for report in test_reports:  # repository returns them in creation order
        if report.work_item_id in considered:
            last_report[report.work_item_id] = report

    last_review: dict[str, Review] = {}
    for review in reviews:
        if review.work_item_id in considered:
            last_review[review.work_item_id] = review

    return GateResults(
        # Vacuously true is not true: a run where nothing ever reached the gate
        # has not passed it.
        technical=bool(last_report)
        and all(report.passed for report in last_report.values()),
        semantic=bool(last_review)
        and all(
            review.verdict is ReviewVerdict.APPROVED for review in last_review.values()
        ),
        technical_reports=len(last_report),
        semantic_reviews=len(last_review),
    )
