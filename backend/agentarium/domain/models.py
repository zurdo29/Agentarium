from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    AgentRole,
    ApprovalStatus,
    OutputStrategy,
    ProjectStatus,
    ReviewVerdict,
    RiskLevel,
    RunOutcome,
    VerificationMode,
    WorkItemStatus,
)

MAX_WORK_ITEM_ATTEMPTS = 25


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


def _reject_unsafe_relative_path(value: str, field: str) -> str:
    # Same discipline as the other local copies of this check
    # (`planning/contracts.py`, `execution/contracts.py`,
    # `benchmarks/functional.py`): kept local to this layer on purpose
    # rather than imported across layers.
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError(f"{field} cannot be blank")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        raise ValueError(f"{field} must be a safe relative path: {value!r}")
    return normalized


class DomainModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=False)


class ProjectBrief(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    summary: str
    scope: list[str]
    deliverables: list[str]
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str]
    approvals_required: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class Project(DomainModel):
    id: str = Field(default_factory=new_id)
    title: str
    goal: str
    status: ProjectStatus = ProjectStatus.DRAFT
    brief: ProjectBrief | None = None
    current_milestone_id: str | None = None
    progress_percent: float = Field(default=0, ge=0, le=100)
    # P3.4 (ADR 0034): whether this project's workspace originated from a
    # repository outside Agentarium's own control. `False` is simply the
    # default compatible with every project that exists today (greenfield,
    # never imported) -- it is not a security posture by itself. The actual
    # fail-closed guarantee lives in ValidationProfileExecutor's
    # NON_EXECUTING_PROFILES gate, which blocks any execution-requiring
    # profile whenever this is `True`. P4.1 (importing an existing repo,
    # not built yet) MUST persist every project it creates with
    # `imported=True` -- see the explicit P4.1 criterion in PLANS.md.
    imported: bool = False
    # P4.1: where an imported project's workspace was copied from, and the
    # commit that was verified consistent at import time. `imported_commit`
    # is what P4.2 uses to export a clean diff/bundle back against the real
    # origin. `imported_source_path` is never read again to re-resolve or
    # re-copy the original's *contents* -- P4.2 does use its persisted
    # string value defensively (isolation/export.py), to reject an export
    # destination that lands inside the original repo, but that is a path
    # comparison against an already-persisted value, never a filesystem
    # read of (or write to) the path it names. Both `None` for every
    # greenfield project, and for any project created before P4.1.
    imported_source_path: str | None = None
    imported_commit: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Milestone(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    title: str
    description: str
    order: int = Field(ge=0)
    completed: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class Dependency(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str
    depends_on_id: str


class ScriptExecutionContract(DomainModel):
    """Entrypoint/args/output a work item declares for SCRIPT_EXECUTION to
    invoke directly, instead of running every delivered `.py` file blind
    with no arguments. See ADR 0027 — nothing constructs one of these today
    (the planning LLM contract deliberately does not expose this field);
    it exists so the validator side of the mechanism is real and tested."""

    entrypoint: str
    args: list[str] = Field(default_factory=list, max_length=20)
    produces: str | None = None

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        normalized = _reject_unsafe_relative_path(value, "entrypoint")
        if not normalized.casefold().endswith(".py"):
            raise ValueError(f"entrypoint must end in .py: {value!r}")
        return normalized

    @field_validator("produces")
    @classmethod
    def validate_produces(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_unsafe_relative_path(value, "produces")

    @field_validator("args")
    @classmethod
    def reject_blank_args(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("args cannot contain blank entries")
        return values


class WorkItem(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    milestone_id: str
    title: str
    description: str
    assignee_role: AgentRole = AgentRole.IMPLEMENTATION_WORKER
    inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str]
    dependency_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str]
    allowed_tools: list[str] = Field(default_factory=list)
    authorized_files: list[str] = Field(default_factory=list)
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=MAX_WORK_ITEM_ATTEMPTS,
    )
    attempt_count: int = Field(default=0, ge=0)
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    priority: int = Field(default=50, ge=0, le=100)
    status: WorkItemStatus = WorkItemStatus.DRAFT
    last_error: str | None = None
    version: int = Field(default=1, ge=1)
    owned_paths: list[str] = Field(default_factory=list)
    shared_component: str | None = None
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE
    # How many automatic splits this task descends from. A planned task is 0;
    # everything an automatic split creates is 1, and only depth 0 may split.
    split_depth: int = Field(default=0, ge=0)
    execution_contract: ScriptExecutionContract | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentDefinition(DomainModel):
    id: str = Field(default_factory=new_id)
    role: AgentRole
    provider: str
    model: str
    temperature: float = Field(ge=0, le=2)
    context_limit: int = Field(gt=0)
    output_token_limit: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)
    tools: list[str]
    allowed_directories: list[str]
    authority: str
    execution_budget: int = Field(gt=0)


class ResourceUsage(DomainModel):
    # duration_ms also covers inter-retry backoff sleep and interpreter/event-loop
    # overhead, so it will not necessarily equal queue_wait_ms + generation_ms.
    duration_ms: int = Field(default=0, ge=0)
    queue_wait_ms: int | None = Field(default=None, ge=0)
    generation_ms: int | None = Field(default=None, ge=0)
    prompt_characters: int = Field(default=0, ge=0)
    response_characters: int = Field(default=0, ge=0)
    prompt_tokens_approx: int = Field(default=0, ge=0)
    response_tokens_approx: int = Field(default=0, ge=0)
    model: str
    provider: str
    errors: int = Field(default=0, ge=0)


class AgentRun(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str | None = None
    agent_role: AgentRole
    model: str
    provider: str
    attempt: int = Field(default=1, ge=1)
    outcome: RunOutcome
    input_summary: str
    output_summary: str
    resource_usage: ResourceUsage
    correlation_id: str
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class Artifact(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str
    agent_run_id: str
    artifact_type: str
    title: str
    content: dict[str, Any]
    file_paths: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"
    checksum: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Review(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str
    artifact_id: str
    reviewer_run_id: str
    verdict: ReviewVerdict
    reasons: list[str]
    acceptance_results: dict[str, bool]
    created_at: datetime = Field(default_factory=utc_now)


class TestReport(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str
    artifact_id: str
    tester_run_id: str
    passed: bool
    # Gate-MVP.2 (ADR 0041): computed by Agentarium from a real executor
    # signal (CommandResult.started), never from the LLM tester's own
    # `checks` -- required, no default, so every construction site must
    # decide it explicitly.
    verification_mode: VerificationMode
    checks: list[dict[str, Any]]
    command_evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    created_at: datetime = Field(default_factory=utc_now)


class Decision(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    title: str
    decision: str
    rationale: str
    reversible: bool
    alternatives: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalRequest(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str | None = None
    action: str
    reason: str
    risk: RiskLevel
    alternatives: list[str]
    affected_resources: list[str]
    status: ApprovalStatus = ApprovalStatus.PENDING
    comments: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class ExecutionEvent(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    work_item_id: str | None = None
    agent_run_id: str | None = None
    agent_role: AgentRole | None = None
    model: str | None = None
    action: str
    message: str
    previous_state: str | None = None
    new_state: str | None = None
    attempt: int | None = None
    error: str | None = None
    resource_usage: ResourceUsage | None = None
    correlation_id: str = Field(default_factory=new_id)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
