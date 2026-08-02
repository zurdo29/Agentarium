from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentarium.domain.enums import OutputStrategy, RiskLevel


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# A single whitespace-free token ending in a real extension (at least one
# letter, so "1.2" or "v0.1" are not mistaken for files). Deliberately narrow:
# prose entries like "resultado", "Documento de arquitectura" or "API REST
# funcionando" must not be read as a file claim.
_PATH_CLAIM_PATTERN = re.compile(r"^[^.\s][^\s]*\.[A-Za-z][A-Za-z0-9]{0,7}$")


def implicit_path_claims(expected_outputs: list[str]) -> list[str]:
    """Paths a task effectively claims through `expected_outputs`.

    Models keep naming the file they are going to write in `expected_outputs`
    and leave `owned_paths` empty (ADR 0023 live evidence), so any mechanical
    ownership check has to read the field they actually use. Entries that are
    not safe relative paths are ignored rather than rejected: this is a
    detection helper, not a contract gate.
    """
    claims: list[str] = []
    seen: set[str] = set()
    for value in expected_outputs:
        candidate = value.strip().replace("\\", "/")
        if not _PATH_CLAIM_PATTERN.match(candidate):
            continue
        if candidate.startswith("/") or ":" in candidate:
            continue
        parts = candidate.split("/")
        if ".." in parts or any(not part for part in parts):
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        claims.append(candidate)
    return claims


def merge_path_claims(owned_paths: list[str], expected_outputs: list[str]) -> list[str]:
    """Every path a task claims: declared `owned_paths` plus implicit ones."""
    claims = list(owned_paths)
    seen = {claim.strip().replace("\\", "/").casefold() for claim in claims}
    for claim in implicit_path_claims(expected_outputs):
        if claim.casefold() in seen:
            continue
        seen.add(claim.casefold())
        claims.append(claim)
    return claims


def _reject_unsafe_owned_paths(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("owned_paths cannot contain blank entries")
    for value in cleaned:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized:
            raise ValueError(f"owned_paths must be relative: {value!r}")
        parts = normalized.split("/")
        if ".." in parts or any(not part for part in parts):
            raise ValueError(f"owned_paths cannot escape the workspace: {value!r}")
    return cleaned


class BriefProposal(PlanningModel):
    summary: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(min_length=1)
    approvals_required: list[str] = Field(default_factory=list)

    @field_validator(
        "scope",
        "deliverables",
        "assumptions",
        "ambiguities",
        "constraints",
        "success_criteria",
        "approvals_required",
    )
    @classmethod
    def reject_blank_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Planning lists cannot contain blank entries")
        return cleaned


class MilestoneProposal(PlanningModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class TaskProposal(PlanningModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    risk: RiskLevel
    priority: int = Field(ge=0, le=100)
    owned_paths: list[str] = Field(default_factory=list, max_length=20)
    shared_component: str | None = Field(default=None, max_length=120)
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE

    @field_validator("dependencies", "expected_outputs", "acceptance_criteria")
    @classmethod
    def reject_blank_task_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Task lists cannot contain blank entries")
        return cleaned

    @field_validator("owned_paths")
    @classmethod
    def validate_owned_paths(cls, values: list[str]) -> list[str]:
        return _reject_unsafe_owned_paths(values)

    def claimed_paths(self) -> list[str]:
        return merge_path_claims(self.owned_paths, self.expected_outputs)


ACCEPTANCE_CRITERION_ID_PATTERN = r"^ac-[1-9][0-9]{0,2}$"


def acceptance_criteria_index(criteria: list[str]) -> list[dict[str, str]]:
    """`ac-1`, `ac-2`, … for the criteria a task must hand to a decompose call.

    The ids exist so a subtask can *point at* a parent criterion instead of
    restating it: a small model rewrites the text almost every time, and the
    rewritten text is what used to make the coverage check fail.
    """
    return [
        {"id": f"ac-{position}", "text": criterion}
        for position, criterion in enumerate(criteria, start=1)
    ]


class SubtaskProposal(PlanningModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    acceptance_criteria_ids: list[str] = Field(default_factory=list, max_length=50)
    owned_paths: list[str] = Field(default_factory=list, max_length=20)
    shared_component: str | None = Field(default=None, max_length=120)
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE

    @field_validator("acceptance_criteria_ids")
    @classmethod
    def validate_criteria_id_shape(cls, values: list[str]) -> list[str]:
        # Shape only. Whether an id actually exists, is unique across the whole
        # decomposition and leaves nothing uncovered can only be decided
        # against the parent task, so it lives in the orchestrator.
        cleaned = [value.strip().casefold() for value in values]
        for value in cleaned:
            if not re.match(ACCEPTANCE_CRITERION_ID_PATTERN, value):
                raise ValueError(
                    f"acceptance_criteria_ids must look like 'ac-1': {value!r}"
                )
        return cleaned

    @field_validator("expected_outputs", "acceptance_criteria")
    @classmethod
    def reject_blank_subtask_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Subtask lists cannot contain blank entries")
        return cleaned

    @field_validator("owned_paths")
    @classmethod
    def validate_owned_paths(cls, values: list[str]) -> list[str]:
        return _reject_unsafe_owned_paths(values)

    def claimed_paths(self) -> list[str]:
        return merge_path_claims(self.owned_paths, self.expected_outputs)


class DecomposeProposal(PlanningModel):
    subtasks: list[SubtaskProposal] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_unique_titles(self) -> DecomposeProposal:
        titles = [subtask.title.strip().casefold() for subtask in self.subtasks]
        if len(set(titles)) != len(titles):
            raise ValueError("Subtask titles must be unique")
        return self

    @model_validator(mode="after")
    def validate_no_unresolved_path_overlap(self) -> DecomposeProposal:
        # All subtasks of one decompose call end up grouped under the same
        # shared_component by _attempt_split regardless of what's declared
        # here (they're siblings from the same split by construction), so
        # matching shared_component isn't required — only an explicit
        # "exclusive" claim on a path another subtask also wants is a real,
        # unresolved conflict. The claim is read from owned_paths *and*
        # expected_outputs: the second is the field models actually use to say
        # "this file is mine" (ADR 0023 live evidence), so looking only at the
        # first made this gate unreachable in practice.
        claimed: dict[str, SubtaskProposal] = {}
        for subtask in self.subtasks:
            for path in subtask.claimed_paths():
                key = path.casefold()
                owner = claimed.get(key)
                if owner is None:
                    claimed[key] = subtask
                    continue
                grouped = (
                    owner.output_strategy != OutputStrategy.EXCLUSIVE
                    and subtask.output_strategy != OutputStrategy.EXCLUSIVE
                )
                if not grouped:
                    raise ValueError(
                        f"path claim overlap between subtasks without a "
                        f"non-exclusive output_strategy on both sides: {path!r}"
                    )
        return self


class PlanProposal(PlanningModel):
    milestone: MilestoneProposal
    tasks: list[TaskProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dag(self) -> PlanProposal:
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("Task keys must be unique")

        titles = [task.title.strip().casefold() for task in self.tasks]
        if len(set(titles)) != len(titles):
            raise ValueError("Task titles must be unique")

        known = set(keys)
        graph = {task.key: task.dependencies for task in self.tasks}
        for task in self.tasks:
            if len(set(task.dependencies)) != len(task.dependencies):
                raise ValueError(f"Task {task.key!r} repeats a dependency")
            if not set(task.dependencies).issubset(known):
                raise ValueError(f"Task {task.key!r} references an unknown dependency")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Task dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for key in keys:
            visit(key)
        return self
