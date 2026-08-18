from __future__ import annotations

from typing import Any

from agentarium.domain.enums import VerificationMode
from agentarium.domain.models import TestReport
from agentarium.services.export_summary import (
    removed_top_level_names,
    render_export_summary_markdown,
)

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
        "removed_top_level_names": {},
        "tester_summary": "Resultado",
        "review_verdict": "approved",
        "review_acceptance_results": {},
    }
    base.update(overrides)
    return base


def _test_report(command_evidence: list[dict[str, Any]]) -> TestReport:
    return TestReport(
        project_id="p1",
        work_item_id="item-1",
        artifact_id="a1",
        tester_run_id="run-1",
        passed=True,
        verification_mode=VerificationMode.STATIC_ONLY,
        checks=[],
        command_evidence=command_evidence,
        summary="Resultado",
    )


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


# -- removed_top_level_names (Gate-MVP.2, ADR 0041) ---------------------------
# The Orchestrator persists one entry per delivered .py file, empty ones
# included, so the evidence trail distinguishes "checked, nothing removed"
# from "never checked". Only the non-empty ones reach export/UI.


def test_removed_top_level_names_extracts_only_the_non_empty_lists() -> None:
    report = _test_report(
        [
            {"check": "materialized_file_checksum", "path": "x.json", "verified": True},
            {"check": "removed_top_level_names", "path": "textkit/slug.py", "removed": []},
            {
                "check": "removed_top_level_names",
                "path": "tests/test_slug.py",
                "removed": ["SlugifyTests", "SlugifyTests.test_strips_accents"],
            },
        ]
    )

    assert removed_top_level_names(report) == {
        "tests/test_slug.py": ["SlugifyTests", "SlugifyTests.test_strips_accents"]
    }


def test_removed_top_level_names_is_empty_without_a_test_report() -> None:
    assert removed_top_level_names(None) == {}


def test_removed_top_level_names_ignores_unrelated_evidence_kinds() -> None:
    """`path` is not exclusive to this check -- materialized_file_checksum
    carries one too, so keying off `check` (not the presence of `path`) is
    what keeps an unrelated entry out."""
    report = _test_report(
        [
            {"check": "materialized_file_checksum", "path": "x.json", "verified": True},
            {"check": "validation_profile", "profile": "python_syntax", "passed": True},
        ]
    )

    assert removed_top_level_names(report) == {}


def test_markdown_renders_removed_names_with_file_and_names() -> None:
    markdown = render_export_summary_markdown(
        _payload(
            [
                _delivered(
                    removed_top_level_names={
                        "tests/test_slug.py": [
                            "SlugifyTests",
                            "SlugifyTests.test_basic_lowercase",
                        ]
                    }
                )
            ]
        )
    )

    assert "Definiciones de nivel superior eliminadas" in markdown
    assert "tests/test_slug.py" in markdown
    assert "SlugifyTests.test_basic_lowercase" in markdown


def test_markdown_omits_the_removed_names_section_when_nothing_was_removed() -> None:
    """A heading that is always present and almost always empty trains the
    reader to skip it -- the normal case must render nothing at all."""
    markdown = render_export_summary_markdown(
        _payload([_delivered(removed_top_level_names={})])
    )

    assert "Definiciones de nivel superior eliminadas" not in markdown
