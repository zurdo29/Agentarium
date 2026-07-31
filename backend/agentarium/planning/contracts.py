from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentarium.domain.enums import OutputStrategy, RiskLevel


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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


class SubtaskProposal(PlanningModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    owned_paths: list[str] = Field(default_factory=list, max_length=20)
    shared_component: str | None = Field(default=None, max_length=120)
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE

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
        # unresolved conflict.
        claimed: dict[str, SubtaskProposal] = {}
        for subtask in self.subtasks:
            for path in subtask.owned_paths:
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
                        f"owned_paths overlap between subtasks without a "
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
