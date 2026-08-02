"""Versioned contract for a benchmark case and for the record of one run.

A case is data, not code: it must stay comparable across months, so it carries
its own `schema_version` and its validators are declarative. Validators read
the integrated files, never the agent's own summary — that is the whole point
of measuring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from .taxonomy import FailureCategory

CASE_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION: Literal[1] = 1


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CaseValidator(BenchmarkModel):
    """One independent check over the delivered files."""

    kind: Literal["file_exists", "file_matches", "file_absent"]
    description: str = Field(min_length=1)
    path_glob: str = Field(min_length=1)
    pattern: str | None = None

    @field_validator("path_glob")
    @classmethod
    def reject_escaping_glob(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError(f"path_glob must stay inside the delivery: {value!r}")
        return normalized

    @field_validator("pattern")
    @classmethod
    def reject_invalid_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:  # pragma: no cover - defensive
            raise ValueError(f"pattern is not a valid regex: {exc}") from exc
        return value

    def check(self, root: Path) -> tuple[bool, str]:
        matches = sorted(path for path in root.glob(self.path_glob) if path.is_file())
        if self.kind == "file_absent":
            if matches:
                return False, f"no debería existir: {matches[0].relative_to(root)}"
            return True, "ausente como se esperaba"
        if not matches:
            return False, f"ningún archivo coincide con {self.path_glob!r}"
        if self.kind == "file_exists":
            return True, f"{len(matches)} archivo(s) coinciden"
        assert self.pattern is not None  # guarded by validate_pattern_is_present
        expression = re.compile(self.pattern)
        for path in matches:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return False, f"no se pudo leer {path.name}: {exc}"
            if expression.search(content):
                return True, f"patrón encontrado en {path.relative_to(root)}"
        return False, f"ningún archivo contiene {self.pattern!r}"


class BenchmarkCase(BenchmarkModel):
    schema_version: int = Field(ge=1, le=CASE_SCHEMA_VERSION)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    expected_artifacts: list[str] = Field(min_length=1)
    validators: list[CaseValidator] = Field(min_length=1)

    @field_validator("validators")
    @classmethod
    def validate_pattern_is_present(
        cls,
        values: list[CaseValidator],
    ) -> list[CaseValidator]:
        for validator in values:
            if validator.kind == "file_matches" and not validator.pattern:
                raise ValueError("file_matches requires a pattern")
            if validator.kind != "file_matches" and validator.pattern:
                raise ValueError(f"{validator.kind} does not take a pattern")
        return values


class ValidatorOutcome(BenchmarkModel):
    description: str
    passed: bool
    detail: str


class BenchmarkRunRecord(BenchmarkModel):
    """One (case, provider, model, repetition) result, appended to the ledger."""

    # Pinned, not merely defaulted: a ledger written by a future format must be
    # refused loudly rather than half-read into today's fields.
    schema_version: Literal[1] = LEDGER_SCHEMA_VERSION
    case_id: str
    case_schema_version: int
    provider: str
    model: str
    repetition: int = Field(ge=1)
    project_id: str | None = None
    project_status: str
    progress_percent: float
    duration_seconds: float
    attempts: int
    splits: int
    human_intervention: bool = False
    # Required and non-empty: an optional field here was a hole in the suite
    # freeze — a record without versions skipped the comparison and let two
    # baselines share one ledger.
    prompt_versions: dict[str, str] = Field(min_length=1)
    technical_result: bool
    semantic_result: bool
    technical_reports: int = 0
    semantic_reviews: int = 0
    validation_passed: bool
    validators: list[ValidatorOutcome] = Field(default_factory=list)
    category: FailureCategory
    evidence: str
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def drop_derived_keys(cls, data: Any) -> Any:
        # `false_completed` is serialized with the record but derived on read.
        # Dropping it here keeps `extra="forbid"` meaningful for genuinely
        # unknown keys while letting a ledger round-trip through this model.
        if isinstance(data, dict) and "false_completed" in data:
            data = {key: value for key, value in data.items() if key != "false_completed"}
        return data

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.case_id, self.provider, self.model, self.repetition)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def false_completed(self) -> bool:
        """The orchestrator said done; the case's own validators disagree.

        Derived from `project_status` and `validation_passed`, and serialized
        with the record so a ledger read by anything else carries it too.
        """
        return self.project_status == "completed" and not self.validation_passed
