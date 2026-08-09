"""Delivery summary built from data already persisted elsewhere, so it
is reproducible without re-deriving anything -- same idea as
`benchmarks/report.py`'s own docstring, and the same
`build_payload`/`render_markdown` split (pure, no I/O, trivially
testable without touching git or SQLite).
"""

from __future__ import annotations

from typing import Any

from agentarium.domain.models import Project, Review, TestReport, WorkItem
from agentarium.isolation.export import ExportManifest


def build_export_summary(
    *,
    project: Project,
    work_items: list[WorkItem],
    reviews: list[Review],
    test_reports: list[TestReport],
    metrics: dict[str, Any],
    integration_events: list[dict[str, Any]],
    manifest: ExportManifest,
) -> dict[str, Any]:
    work_items_by_id = {item.id: item for item in work_items}
    # list_reviews()/list_test_reports() are already ordered by
    # created_at ascending (repository.py) -- the same "last one wins"
    # idiom BenchmarkLedger.latest_by_key() already uses elsewhere. A
    # WorkItem reaches COMPLETED exactly once and nothing runs after
    # acceptance, so the chronologically-last record for a completed
    # item's work_item_id is unambiguously the one that got merged.
    latest_review_by_work_item = {review.work_item_id: review for review in reviews}
    latest_test_report_by_work_item = {report.work_item_id: report for report in test_reports}
    exported_commit_shas = {commit.commit for commit in manifest.commits}

    delivered_work_items: list[dict[str, Any]] = []
    matched_in_git_history = 0
    for event in integration_events:
        metadata = event.get("metadata") or {}
        integration_commit = metadata.get("integration_commit")
        in_range = integration_commit in exported_commit_shas
        if in_range:
            matched_in_git_history += 1
        event_work_item_id = str(event.get("work_item_id") or "")
        work_item = work_items_by_id.get(event_work_item_id)
        review = latest_review_by_work_item.get(event_work_item_id)
        test_report = latest_test_report_by_work_item.get(event_work_item_id)
        delivered_work_items.append(
            {
                "work_item_id": event_work_item_id,
                "title": work_item.title if work_item else None,
                "status": work_item.status.value if work_item else None,
                "acceptance_criteria": work_item.acceptance_criteria if work_item else [],
                "attempt_count": work_item.attempt_count if work_item else None,
                "branch": metadata.get("branch"),
                "candidate_commit": metadata.get("candidate_commit"),
                "integration_commit": integration_commit,
                "files": metadata.get("files", []),
                "in_exported_range": in_range,
                "tester_passed": test_report.passed if test_report else None,
                "tester_summary": test_report.summary if test_report else None,
                "review_verdict": review.verdict.value if review else None,
                "review_acceptance_results": review.acceptance_results if review else {},
            }
        )

    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "goal": project.goal,
            "imported": project.imported,
            "imported_source_path": project.imported_source_path,
            "imported_commit": project.imported_commit,
        },
        "range": {
            "base_commit": manifest.base_commit,
            "base_is_import_commit": manifest.base_is_import_commit,
            "head_commit": manifest.head_commit,
            "commit_count": manifest.commit_count,
            "commits": [
                {
                    "commit": commit.commit,
                    "author": commit.author,
                    "authored_at": commit.authored_at,
                    "subject": commit.subject,
                }
                for commit in manifest.commits
            ],
            "files_changed": list(manifest.files_changed),
        },
        "delivered_work_items": delivered_work_items,
        "totals": metrics,
        "consistency": {
            "integration_events_total": len(integration_events),
            "integration_events_matched_in_git_history": matched_in_git_history,
            "matches_git_history": matched_in_git_history == len(integration_events),
        },
    }


def render_export_summary_markdown(payload: dict[str, Any]) -> str:
    project = payload["project"]
    range_ = payload["range"]
    consistency = payload["consistency"]

    lines = [
        f"# Exportación: {project['title']}",
        "",
        f"- Objetivo: {project['goal']}",
    ]
    if project["imported"]:
        lines.append(f"- Importado desde: `{project['imported_source_path']}`")
        lines.append(f"- Commit importado: `{project['imported_commit']}`")
    lines += [
        "",
        "## Rango exportado",
        "",
        f"- Base: `{range_['base_commit']}`"
        f" ({'commit importado' if range_['base_is_import_commit'] else 'raíz del repositorio'})",
        f"- HEAD: `{range_['head_commit']}`",
        f"- Commits: **{range_['commit_count']}**",
        f"- Archivos modificados: **{len(range_['files_changed'])}**",
        "",
        "## Consistencia",
        "",
        f"- Eventos de integración: **{consistency['integration_events_total']}**",
        "- Presentes en el historial exportado: "
        f"**{consistency['integration_events_matched_in_git_history']}**",
        f"- Coincide con el historial de git: "
        f"**{'sí' if consistency['matches_git_history'] else 'NO'}**",
        "",
    ]

    delivered = payload["delivered_work_items"]
    if delivered:
        lines += [
            "## Entregas",
            "",
            "| Tarea | Estado | Tester | Revisor | Commit | En rango |",
            "|---|---|---|---|---|---|",
        ]
        for item in delivered:
            tester = "ok" if item["tester_passed"] else "falla"
            reviewer = item["review_verdict"] or "—"
            commit = (item["integration_commit"] or "")[:12] or "—"
            in_range = "sí" if item["in_exported_range"] else "NO"
            lines.append(
                f"| {item['title'] or item['work_item_id']} | {item['status'] or '—'} | "
                f"{tester} | {reviewer} | `{commit}` | {in_range} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"
