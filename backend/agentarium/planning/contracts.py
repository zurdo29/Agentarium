from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentarium.domain.enums import RiskLevel


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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

    @field_validator("dependencies", "expected_outputs", "acceptance_criteria")
    @classmethod
    def reject_blank_task_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Task lists cannot contain blank entries")
        return cleaned


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
