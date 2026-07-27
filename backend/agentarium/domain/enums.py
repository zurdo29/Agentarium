from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItemStatus(StrEnum):
    DRAFT = "draft"
    BLOCKED = "blocked"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    CHANGES_REQUESTED = "changes_requested"
    AWAITING_APPROVAL = "awaiting_approval"
    PASSED = "passed"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AgentRole(StrEnum):
    DIRECTOR = "director"
    TECHNICAL_MANAGER = "technical_manager"
    IMPLEMENTATION_WORKER = "implementation_worker"
    TESTER = "tester"
    CRITICAL_REVIEWER = "critical_reviewer"


class RunOutcome(StrEnum):
    ARTIFACT_DELIVERED = "artifact_delivered"
    NEEDS_INFORMATION = "needs_information"
    BLOCKED = "blocked"
    APPROVAL_REQUESTED = "approval_requested"
    ESCALATED = "escalated"
    REJECTED = "rejected"


class ReviewVerdict(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
