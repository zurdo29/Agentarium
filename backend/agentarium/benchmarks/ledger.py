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


def dump_json_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
