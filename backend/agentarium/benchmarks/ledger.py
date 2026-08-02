"""Append-only record of finished runs, so a matrix can be resumed.

A full matrix is hours of inference. It must survive a crash, a reboot or a
stopped Ollama without repeating combinations that already produced a result —
and without ever silently rewriting one.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .contracts import BenchmarkRunRecord

RunKey = tuple[str, str, str, int]


class SuiteDrift(ValueError):
    """The suite no longer measures what its existing records measured."""


class BenchmarkLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def completed_keys(self) -> set[RunKey]:
        return {record.key for record in self.records()}

    def records(self) -> list[BenchmarkRunRecord]:
        if not self.path.is_file():
            return []
        records: list[BenchmarkRunRecord] = []
        for number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(BenchmarkRunRecord.model_validate_json(stripped))
            except ValidationError as exc:
                raise ValueError(
                    f"{self.path.name}: línea {number} no es un registro válido: {exc}"
                ) from exc
        return records

    def append(self, record: BenchmarkRunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json() + "\n"
        # Append mode with an explicit flush: a run that dies mid-matrix must
        # leave every previous result intact and readable.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()

    def latest_by_key(self) -> dict[RunKey, BenchmarkRunRecord]:
        """Last record wins, so a deliberate re-run supersedes an old one."""
        latest: dict[RunKey, BenchmarkRunRecord] = {}
        for record in self.records():
            latest[record.key] = record
        return latest

    def assert_comparable(
        self,
        *,
        case_versions: dict[str, int],
        prompt_versions: dict[str, str],
    ) -> None:
        """Refuse to append measurements that are not comparable to the rest.

        Resumability keys on (case, provider, model, repetition). If the case
        definition or a prompt changed underneath, that key names a *different*
        measurement, and skipping it as "already done" would quietly mix two
        baselines into one report. Fail early and ask for a new suite.
        """
        drift: list[str] = []
        for record in self.records():
            expected_case = case_versions.get(record.case_id)
            if expected_case is not None and expected_case != record.case_schema_version:
                drift.append(
                    f"caso {record.case_id}: el ledger tiene "
                    f"schema_version={record.case_schema_version}, ahora es "
                    f"{expected_case}"
                )
            if record.prompt_versions != prompt_versions:
                changed = sorted(
                    f"{name}: {record.prompt_versions.get(name, '—')} → "
                    f"{prompt_versions.get(name, '—')}"
                    for name in set(record.prompt_versions) | set(prompt_versions)
                    if record.prompt_versions.get(name) != prompt_versions.get(name)
                )
                drift.append("prompts: " + ", ".join(changed))
            if drift:
                break
        if drift:
            raise SuiteDrift(
                "Esta suite ya contiene mediciones hechas con otra versión "
                f"({'; '.join(drift)}). Usá --suite con un nombre nuevo para "
                "no mezclar dos baselines en un mismo informe."
            )


def dump_json_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
