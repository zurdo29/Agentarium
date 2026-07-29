from __future__ import annotations

from typing import Any

from agentarium.domain.enums import ReviewVerdict
from agentarium.domain.models import WorkItem
from agentarium.execution.validation import VALIDATION_CONTRACT_VERSION
from agentarium.repositories import Repository


class ContextBuilder:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def operational(self, item: WorkItem) -> dict[str, Any]:
        reviews = self.repository.list_reviews(item.project_id)
        approved_dependency_artifact_ids = {
            review.artifact_id
            for review in reviews
            if review.work_item_id in item.dependency_ids
            and review.verdict is ReviewVerdict.APPROVED
        }
        dependency_criteria = {
            dependency_id: set(
                self.repository.get_work_item(dependency_id).acceptance_criteria
            )
            for dependency_id in item.dependency_ids
        }
        approved_dependency_artifacts = [
            artifact.model_dump(mode="json")
            for artifact in self.repository.list_artifacts(item.project_id)
            if artifact.id in approved_dependency_artifact_ids
            and dependency_criteria.get(artifact.work_item_id, set()).issubset(
                set(artifact.content.get("acceptance_criteria_addressed", []))
            )
        ]
        prior_review_feedback = [
            {
                "attempt_artifact_id": review.artifact_id,
                "reasons": review.reasons,
                "acceptance_results": review.acceptance_results,
            }
            for review in reviews
            if review.work_item_id == item.id
        ]
        prior_validation_failures: list[dict[str, Any]] = [
            {
                "attempt_artifact_id": report.artifact_id,
                "failures": [
                    {
                        "profile": str(evidence.get("profile", "unknown")),
                        "detail": str(
                            evidence.get("stderr")
                            or evidence.get("stdout")
                            or "Validation profile failed"
                        )[:4000],
                    }
                    for evidence in report.command_evidence
                    if evidence.get("check") == "validation_profile"
                    and evidence.get("contract_version")
                    == VALIDATION_CONTRACT_VERSION
                    and not bool(evidence.get("passed"))
                ],
            }
            for report in self.repository.list_test_reports(item.project_id)
            if report.work_item_id == item.id
            and any(
                evidence.get("check") == "validation_profile"
                and evidence.get("contract_version")
                == VALIDATION_CONTRACT_VERSION
                and not bool(evidence.get("passed"))
                for evidence in report.command_evidence
            )
        ]
        cumulative_validation_requirements: list[str] = []
        for entry in prior_validation_failures:
            for failure in entry["failures"]:
                for line in str(failure["detail"]).splitlines():
                    cleaned = line.strip().removeprefix("-").strip()
                    if (
                        cleaned
                        and cleaned not in cumulative_validation_requirements
                    ):
                        cumulative_validation_requirements.append(cleaned)
        is_retry = item.attempt_count > 1
        current_task_artifacts = [
            artifact
            for artifact in self.repository.list_artifacts(item.project_id)
            if artifact.work_item_id == item.id
        ]
        latest_candidate_files: list[dict[str, Any]] = []
        if is_retry and current_task_artifacts:
            latest_files = current_task_artifacts[-1].content.get("files", [])
            if isinstance(latest_files, list):
                latest_candidate_files = [
                    {
                        "path": str(file.get("path", "")),
                        "content": str(file.get("content", ""))[:16_000],
                        "purpose": str(file.get("purpose", "")),
                    }
                    for file in latest_files
                    if isinstance(file, dict)
                    and str(file.get("path", "")).strip()
                    and str(file.get("content", "")).strip()
                ][:12]
        return {
            "task": item.model_dump(mode="json"),
            "retry_guidance": {
                "is_retry": is_retry,
                "instruction": (
                    "Produce una entrega nueva para la tarea actual. No vuelvas a "
                    "entregar un artefacto de dependencia y corrige cada criterio "
                    "fallido del intento anterior. Corrige literalmente cada entrada "
                    "de prior_validation_failures; son comprobaciones obligatorias."
                ),
                "prior_review_feedback": prior_review_feedback[-3:],
                "prior_validation_failures": prior_validation_failures[-3:],
                "cumulative_validation_requirements": (
                    cumulative_validation_requirements[-20:]
                ),
                "prior_candidate_files": latest_candidate_files,
            },
            "dependency_artifacts": (
                [] if is_retry else approved_dependency_artifacts
            ),
            "dependency_references": [
                {
                    "id": artifact["id"],
                    "title": artifact["title"],
                    "summary": artifact["content"].get("summary", ""),
                    "file_paths": [
                        file.get("path", "")
                        for file in artifact["content"].get("files", [])
                    ],
                }
                for artifact in approved_dependency_artifacts
            ],
            "project": self.project(item.project_id),
        }

    def project(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        return {
            "brief": project.brief.model_dump(mode="json") if project.brief else None,
            "decisions": [
                decision.model_dump(mode="json")
                for decision in self.repository.list_decisions(project_id)
            ],
            "milestones": [
                milestone.model_dump(mode="json")
                for milestone in self.repository.list_milestones(project_id)
            ],
            "progress_percent": project.progress_percent,
        }
