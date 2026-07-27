from __future__ import annotations

from typing import Any

from agentarium.domain.models import WorkItem
from agentarium.repositories import Repository


class ContextBuilder:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def operational(self, item: WorkItem) -> dict[str, Any]:
        dependency_artifacts = [
            artifact.model_dump(mode="json")
            for artifact in self.repository.list_artifacts(item.project_id)
            if artifact.work_item_id in item.dependency_ids
        ]
        return {
            "task": item.model_dump(mode="json"),
            "dependency_artifacts": dependency_artifacts,
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
