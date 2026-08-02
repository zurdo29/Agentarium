"""Report built from the ledger alone, so it is reproducible without re-running."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .contracts import BenchmarkRunRecord
from .taxonomy import FailureCategory


def build_payload(records: list[BenchmarkRunRecord]) -> dict[str, Any]:
    total = len(records)
    completed = sum(1 for record in records if record.category is FailureCategory.COMPLETED)
    false_completed = [record for record in records if record.false_completed]
    categories = Counter(record.category.value for record in records)
    by_target: dict[str, dict[str, Any]] = {}
    for record in records:
        label = f"{record.provider}:{record.model}" if record.model else record.provider
        bucket = by_target.setdefault(
            label,
            {"runs": 0, "completed": 0, "false_completed": 0, "seconds": 0.0},
        )
        bucket["runs"] += 1
        bucket["completed"] += record.category is FailureCategory.COMPLETED
        bucket["false_completed"] += record.false_completed
        bucket["seconds"] += record.duration_seconds
    for bucket in by_target.values():
        bucket["completion_rate"] = _rate(bucket["completed"], bucket["runs"])
        bucket["average_seconds"] = round(bucket["seconds"] / bucket["runs"], 2)
        del bucket["seconds"]
    return {
        "totals": {
            "runs": total,
            "completed": completed,
            "completion_rate": _rate(completed, total),
            "false_completed": len(false_completed),
        },
        "categories": dict(sorted(categories.items(), key=lambda item: (-item[1], item[0]))),
        "by_target": dict(sorted(by_target.items())),
        "false_completed_runs": [
            {
                "case_id": record.case_id,
                "provider": record.provider,
                "model": record.model,
                "repetition": record.repetition,
                "project_id": record.project_id,
                "failed_validators": [
                    outcome.description
                    for outcome in record.validators
                    if not outcome.passed
                ],
            }
            for record in false_completed
        ],
        "runs": [record.model_dump(mode="json") for record in records],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    totals = payload["totals"]
    lines = [
        "# Informe de benchmark",
        "",
        f"- Corridas registradas: **{totals['runs']}**",
        f"- Completadas y validadas: **{totals['completed']}** "
        f"({totals['completion_rate']}%)",
        f"- Falsos `completed`: **{totals['false_completed']}**",
        "",
    ]

    if totals["runs"] == 0:
        lines.append("No hay corridas en el ledger todavía.")
        return "\n".join(lines) + "\n"

    lines += [
        "## Por modelo",
        "",
        "| Objetivo | Corridas | Completadas | Tasa | Falsos | Segundos (media) |",
        "|---|---|---|---|---|---|",
    ]
    for label, bucket in payload["by_target"].items():
        lines.append(
            f"| `{label}` | {bucket['runs']} | {bucket['completed']} | "
            f"{bucket['completion_rate']}% | {bucket['false_completed']} | "
            f"{bucket['average_seconds']} |"
        )

    lines += ["", "## Categorías de resultado", "", "| Categoría | Corridas |", "|---|---|"]
    for category, count in payload["categories"].items():
        lines.append(f"| `{category}` | {count} |")

    if payload["false_completed_runs"]:
        lines += [
            "",
            "## Falsos `completed`",
            "",
            "El orquestador dio la entrega por buena y los validadores del caso "
            "la rechazaron. Este número debe ser cero.",
            "",
            "| Caso | Objetivo | Rep. | Validadores que fallaron |",
            "|---|---|---|---|",
        ]
        for entry in payload["false_completed_runs"]:
            label = f"{entry['provider']}:{entry['model']}" if entry["model"] else entry["provider"]
            failed = "; ".join(entry["failed_validators"]) or "—"
            lines.append(
                f"| `{entry['case_id']}` | `{label}` | {entry['repetition']} | {failed} |"
            )

    lines += [
        "",
        "## Detalle por corrida",
        "",
        "| Caso | Objetivo | Rep. | Estado | Categoría | Validación | "
        "Intentos | Divisiones | Segundos |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for run in payload["runs"]:
        label = f"{run['provider']}:{run['model']}" if run["model"] else run["provider"]
        lines.append(
            f"| `{run['case_id']}` | `{label}` | {run['repetition']} | "
            f"{run['project_status']} | `{run['category']}` | "
            f"{'ok' if run['validation_passed'] else 'falla'} | "
            f"{run['attempts']} | {run['splits']} | {run['duration_seconds']} |"
        )
    return "\n".join(lines) + "\n"


def _rate(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0
