from __future__ import annotations

from typing import Any

from agentarium.services.export_summary import render_export_summary_markdown

# Gate-MVP.2 (ADR 0041): export_summary.py has no dedicated test file yet
# (only exercised indirectly via test_export_project_service.py's real
# pipeline run) -- this covers just the new tester-status branching in
# render_export_summary_markdown, matching the module's own "pure, no I/O"
# docstring rather than driving a full orchestrator run for a rendering
# concern.


def _payload(delivered_work_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "project": {
            "title": "Proyecto",
            "goal": "Objetivo",
            "imported": False,
            "imported_source_path": None,
            "imported_commit": None,
        },
        "range": {
            "base_commit": "a" * 40,
            "base_is_import_commit": False,
            "head_commit": "b" * 40,
            "commit_count": 1,
            "commits": [],
            "files_changed": [],
        },
        "delivered_work_items": delivered_work_items,
        "totals": {"completed": len(delivered_work_items)},
        "consistency": {
            "integration_events_total": len(delivered_work_items),
            "integration_events_matched_in_git_history": len(delivered_work_items),
            "matches_git_history": True,
        },
    }


def _delivered(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "work_item_id": "item-1",
        "title": "Tarea",
        "status": "completed",
        "acceptance_criteria": [],
        "attempt_count": 1,
        "branch": "task/item-1",
        "candidate_commit": "c" * 40,
        "integration_commit": "d" * 40,
        "files": ["result.py"],
        "in_exported_range": True,
        "tester_passed": True,
        "tester_verification_mode": "executed",
        "tester_summary": "Resultado",
        "review_verdict": "approved",
        "review_acceptance_results": {},
    }
    base.update(overrides)
    return base


def test_static_only_evidence_never_reads_as_executed_ok() -> None:
    markdown = render_export_summary_markdown(
        _payload([_delivered(tester_passed=True, tester_verification_mode="static_only")])
    )

    assert "sólo estática" in markdown
    assert "ejecutada: ok" not in markdown


def test_executed_and_passing_reads_as_executed_ok() -> None:
    markdown = render_export_summary_markdown(
        _payload([_delivered(tester_passed=True, tester_verification_mode="executed")])
    )

    assert "ejecutada: ok" in markdown
    assert "sólo estática" not in markdown


def test_executed_and_failing_reads_as_executed_falla_not_static() -> None:
    markdown = render_export_summary_markdown(
        _payload([_delivered(tester_passed=False, tester_verification_mode="executed")])
    )

    assert "ejecutada: falla" in markdown
    assert "sólo estática" not in markdown
