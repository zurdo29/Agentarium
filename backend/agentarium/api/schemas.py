from __future__ import annotations

from pydantic import BaseModel, Field

from agentarium.domain.enums import ApprovalStatus


class CreateProjectRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=20_000)
    title: str | None = Field(default=None, max_length=240)
    auto_plan: bool = True


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
