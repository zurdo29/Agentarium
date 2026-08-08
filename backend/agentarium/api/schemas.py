from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agentarium.domain.enums import ApprovalStatus


class CreateProjectRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=20_000)
    title: str | None = Field(default=None, max_length=240)
    auto_plan: bool = True


def _validate_absolute_source_path(value: str) -> str:
    # P4.1: the opposite check from WorkspaceFileProposal.validate_relative_path
    # (execution/contracts.py) on purpose -- that one guards paths meant to
    # stay inside the workspace; this one guards a path meant to live
    # outside it. A relative path here is ambiguous (relative to the
    # backend process's cwd is not an acceptable answer), and this is the
    # first field in the API that is ever supposed to reference something
    # outside workspace_root.
    raw = value.strip()
    if not raw:
        raise ValueError("source_path cannot be blank")
    if not (PureWindowsPath(raw).is_absolute() or PurePosixPath(raw).is_absolute()):
        raise ValueError("source_path must be an absolute path")
    return raw


class InspectImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=1000)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _validate_absolute_source_path(value)


class ImportProjectRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=1000)
    goal: str = Field(min_length=3, max_length=20_000)
    title: str | None = Field(default=None, max_length=240)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _validate_absolute_source_path(value)


class ResolveApprovalRequest(BaseModel):
    status: ApprovalStatus
    comments: str | None = Field(default=None, max_length=4000)


class CreateApprovalRequest(BaseModel):
    project_id: str
    work_item_id: str | None = None
    action: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=3, max_length=4000)
    affected_resources: list[str] = Field(default_factory=list)


class PriorityRequest(BaseModel):
    priority: int = Field(ge=0, le=100)


class EscalateRequest(BaseModel):
    reason: str = Field(
        default="La tarea necesita una decisión o intervención de autoridad superior.",
        min_length=3,
        max_length=4000,
    )


class ReworkRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=4000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)


class CandidateFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)
    purpose: str = Field(min_length=1, max_length=1000)


class SubmitCandidateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=4000)
    files: list[CandidateFileRequest] = Field(min_length=1, max_length=12)


class RuntimeCapabilities(BaseModel):
    model_inference: bool
    project_files: bool
    command_execution: bool
    change_isolation: bool


class RuntimeStatus(BaseModel):
    mode: Literal["simulation", "artifact_only", "workspace"]
    providers: list[str]
    active_model: str | None = None
    capabilities: RuntimeCapabilities


class ProviderSelectionRequest(BaseModel):
    provider: Literal["mock", "ollama", "openai_compatible"]
    model: str | None = Field(default=None, max_length=240)
