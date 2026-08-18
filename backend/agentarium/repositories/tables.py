from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    brief_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    current_milestone_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0)
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MilestoneRow(Base):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkItemRow(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    milestone_id: Mapped[str] = mapped_column(
        ForeignKey("milestones.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    assignee_role: Mapped[str] = mapped_column(String(50))
    inputs_json: Mapped[list[str]] = mapped_column(JSON)
    expected_outputs_json: Mapped[list[str]] = mapped_column(JSON)
    acceptance_criteria_json: Mapped[list[str]] = mapped_column(JSON)
    allowed_tools_json: Mapped[list[str]] = mapped_column(JSON)
    authorized_files_json: Mapped[list[str]] = mapped_column(JSON)
    max_attempts: Mapped[int] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    risk: Mapped[str] = mapped_column(String(20))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(40), index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    owned_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    shared_component: Mapped[str | None] = mapped_column(String(120), nullable=True)
    output_strategy: Mapped[str] = mapped_column(String(20), default="exclusive")
    split_depth: Mapped[int] = mapped_column(Integer, default=0)
    execution_contract_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DependencyRow(Base):
    __tablename__ = "dependencies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_role: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(50))
    input_summary: Mapped[str] = mapped_column(Text)
    output_summary: Mapped[str] = mapped_column(Text)
    resource_usage_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    artifact_type: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(240))
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    file_paths_json: Mapped[list[str]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(20))
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewRow(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    reviewer_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    verdict: Mapped[str] = mapped_column(String(40))
    reasons_json: Mapped[list[str]] = mapped_column(JSON)
    acceptance_results_json: Mapped[dict[str, bool]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TestReportRow(Base):
    __tablename__ = "test_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    tester_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    passed: Mapped[bool] = mapped_column(Boolean)
    verification_mode: Mapped[str] = mapped_column(String(20))
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    command_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    decision: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    reversible: Mapped[bool] = mapped_column(Boolean)
    alternatives_json: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(20))
    alternatives_json: Mapped[list[str]] = mapped_column(JSON)
    affected_resources_json: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), index=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventRow(Base):
    __tablename__ = "execution_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    message: Mapped[str] = mapped_column(Text)
    previous_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
