from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceFileProposal(ExecutionModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=200_000)
    purpose: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        raw = value.strip()
        path = PurePosixPath(raw.replace("\\", "/"))
        windows_path = PureWindowsPath(raw)
        if (
            not raw
            or path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0].casefold() == "workspaces"
        ):
            raise ValueError("Workspace file paths must be safe relative paths")
        protected = {".agents", ".codex", ".git", ".openai", ".ssh"}
        if any(part.casefold() in protected for part in path.parts):
            raise ValueError("Workspace file path targets a protected directory")
        filename = path.name.casefold()
        if filename == ".env" or filename.startswith(".env."):
            raise ValueError("Workspace file path targets an environment file")
        return path.as_posix()

    @field_validator("purpose")
    @classmethod
    def normalize_purpose(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Workspace file purpose cannot be blank")
        return cleaned

    @field_validator("content")
    @classmethod
    def reject_placeholder_content(cls, value: str) -> str:
        placeholder_patterns = (
            r"\bTODO\b",
            r"\bFIXME\b",
            r"(?<!:)\bplaceholder\b(?!\s*=)",
            r"\blogic(?:a)?\s+here\b",
            r"\bl[oó]gica\b[^\r\n]{0,80}\baqu[ií]\b",
            r"\baqu[ií]\s+se\s+(?:agregar|implement)",
        )
        if any(
            re.search(pattern, value, flags=re.IGNORECASE)
            for pattern in placeholder_patterns
        ):
            raise ValueError(
                "Workspace file content must implement behavior, not placeholders"
            )
        return value


class WorkArtifactProposal(ExecutionModel):
    artifact_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=4000)
    quality: Literal["draft", "verified"]
    evidence: list[str] = Field(default_factory=list, max_length=20)
    dependency_artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    acceptance_criteria_addressed: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    files: list[WorkspaceFileProposal] = Field(min_length=1, max_length=12)

    @field_validator("artifact_type", "title", "summary")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Artifact text fields cannot be blank")
        return cleaned

    @field_validator(
        "evidence",
        "dependency_artifact_ids",
        "acceptance_criteria_addressed",
        "limitations",
    )
    @classmethod
    def reject_blank_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Artifact lists cannot contain blank entries")
        return cleaned

    @model_validator(mode="after")
    def reject_duplicate_paths(self) -> WorkArtifactProposal:
        paths = [file.path.casefold() for file in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Workspace file paths must be unique")
        return self


class TestCheckProposal(ExecutionModel):
    name: str = Field(min_length=1, max_length=160)
    passed: bool
    evidence: str = Field(min_length=1, max_length=2000)


class TestEvaluationProposal(ExecutionModel):
    passed: bool
    checks: list[TestCheckProposal] = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=4000)


class ReviewEvaluationProposal(ExecutionModel):
    verdict: Literal["approved", "changes_requested"]
    reasons: list[str] = Field(min_length=1, max_length=20)
    acceptance_results: dict[str, bool] = Field(min_length=1, max_length=50)

    @field_validator("reasons")
    @classmethod
    def reject_blank_reasons(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Review reasons cannot contain blank entries")
        return cleaned

    @field_validator("acceptance_results")
    @classmethod
    def reject_blank_criteria(cls, values: dict[str, bool]) -> dict[str, bool]:
        cleaned = {key.strip(): value for key, value in values.items()}
        if any(not key for key in cleaned):
            raise ValueError("Acceptance result keys cannot be blank")
        return cleaned
