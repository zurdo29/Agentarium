from __future__ import annotations

from typing import Any, cast

from agentarium.domain.enums import ReviewVerdict, VerificationMode
from agentarium.domain.models import Artifact, Review, WorkItem
from agentarium.domain.models import TestReport as ReportModel
from agentarium.execution.capabilities import RuntimeCapabilityManifest
from agentarium.execution.validation import VALIDATION_CONTRACT_VERSION
from agentarium.memory.context import ContextBuilder
from agentarium.repositories import Repository


def _capabilities() -> RuntimeCapabilityManifest:
    return RuntimeCapabilityManifest(
        python_version="3.14.0",
        executables_allowed=["git", "python"],
        executables_available=["git", "python"],
    )


class ContextRepository:
    def __init__(
        self,
        artifacts: list[Artifact],
        reviews: list[Review],
        work_items: list[WorkItem],
        reports: list[ReportModel] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.reviews = reviews
        self.work_items = {item.id: item for item in work_items}
        self.reports = reports or []
        self.events = events or []

    def list_artifacts(self, _project_id: str) -> list[Artifact]:
        return self.artifacts

    def list_reviews(self, _project_id: str) -> list[Review]:
        return self.reviews

    def get_work_item(self, work_item_id: str) -> WorkItem:
        return self.work_items[work_item_id]

    def list_test_reports(self, _project_id: str) -> list[ReportModel]:
        return self.reports

    def list_events_for_work_item(
        self, _project_id: str, work_item_id: str, *, limit: int = 250
    ) -> list[dict[str, Any]]:
        return [
            event for event in self.events if event.get("work_item_id") == work_item_id
        ][:limit]


def test_operational_context_uses_only_approved_dependencies_and_retry_feedback(
    monkeypatch: Any,
) -> None:
    accepted = Artifact(
        id="accepted",
        project_id="project",
        work_item_id="dependency",
        agent_run_id="worker-1",
        artifact_type="Design",
        title="Diseño aprobado",
        content={
            "files": [],
            "acceptance_criteria_addressed": ["Diseño listo"],
        },
    )
    rejected = Artifact(
        id="rejected",
        project_id="project",
        work_item_id="dependency",
        agent_run_id="worker-2",
        artifact_type="Design",
        title="Diseño rechazado",
        content={
            "files": [],
            "acceptance_criteria_addressed": ["Diseño listo"],
        },
    )
    previous_candidate = Artifact(
        id="previous-attempt",
        project_id="project",
        work_item_id="current",
        agent_run_id="worker-current",
        artifact_type="Code",
        title="Incomplete game",
        content={
            "files": [
                {
                    "path": "game.js",
                    "content": "let score = 0;",
                    "purpose": "Game logic",
                }
            ],
            "acceptance_criteria_addressed": ["Juego funcional"],
        },
    )
    reviews = [
        Review(
            project_id="project",
            work_item_id="dependency",
            artifact_id=accepted.id,
            reviewer_run_id="reviewer-1",
            verdict=ReviewVerdict.APPROVED,
            reasons=["Cumple."],
            acceptance_results={"Diseño listo": True},
        ),
        Review(
            project_id="project",
            work_item_id="current",
            artifact_id="previous-attempt",
            reviewer_run_id="reviewer-2",
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            reasons=["Falta lógica jugable."],
            acceptance_results={"Juego funcional": False},
        ),
    ]
    dependency = WorkItem(
        id="dependency",
        project_id="project",
        milestone_id="milestone",
        title="Diseñar",
        description="Crear diseño",
        expected_outputs=["Diseño"],
        acceptance_criteria=["Diseño listo"],
    )
    report = ReportModel(
        project_id="project",
        work_item_id="current",
        artifact_id="previous-attempt",
        tester_run_id="tester",
        passed=False,
        verification_mode=VerificationMode.STATIC_ONLY,
        checks=[],
        command_evidence=[
            {
                "check": "validation_profile",
                "profile": "web_application",
                "passed": False,
                "contract_version": VALIDATION_CONTRACT_VERSION,
                "stderr": "Pac-Man criterion requires score",
            }
        ],
        summary="Falla objetiva.",
    )
    repository = ContextRepository(
        [accepted, rejected, previous_candidate],
        reviews,
        [dependency],
        [report],
    )
    builder = ContextBuilder(cast(Repository, repository), _capabilities())
    monkeypatch.setattr(builder, "project", lambda _project_id: {"brief": {}})
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear juego",
        description="Implementar lógica",
        expected_outputs=["Código"],
        dependency_ids=["dependency"],
        acceptance_criteria=["Juego funcional"],
        attempt_count=2,
    )

    context = builder.operational(item)

    assert context["dependency_artifacts"] == []
    assert context["dependency_references"] == [
        {
            "id": "accepted",
            "title": "Diseño aprobado",
            "summary": "",
            "file_paths": [],
        }
    ]
    assert context["retry_guidance"]["is_retry"] is True
    assert context["retry_guidance"]["prior_review_feedback"] == [
        {
            "attempt_artifact_id": "previous-attempt",
            "reasons": ["Falta lógica jugable."],
            "acceptance_results": {"Juego funcional": False},
        }
    ]
    assert context["retry_guidance"]["prior_validation_failures"] == [
        {
            "attempt_artifact_id": "previous-attempt",
            "failures": [
                {
                    "profile": "web_application",
                    "detail": "Pac-Man criterion requires score",
                }
            ],
        }
    ]
    assert context["retry_guidance"]["cumulative_validation_requirements"] == [
        "Pac-Man criterion requires score"
    ]
    assert context["retry_guidance"]["prior_candidate_files"] == [
        {
            "path": "game.js",
            "content": "let score = 0;",
            "purpose": "Game logic",
        }
    ]

    first_attempt = item.model_copy(
        update={"id": "fresh", "attempt_count": 1}
    )
    first_context = builder.operational(first_attempt)
    assert [artifact["id"] for artifact in first_context["dependency_artifacts"]] == [
        "accepted"
    ]
    assert first_context["retry_guidance"]["prior_candidate_files"] == []


def test_operational_context_excludes_dependency_with_foreign_criteria(
    monkeypatch: Any,
) -> None:
    artifact = Artifact(
        id="legacy-approved",
        project_id="project",
        work_item_id="dependency",
        agent_run_id="worker",
        artifact_type="Design",
        title="Entrega de otra tarea",
        content={
            "files": [],
            "acceptance_criteria_addressed": ["Criterio ajeno"],
        },
    )
    review = Review(
        project_id="project",
        work_item_id="dependency",
        artifact_id=artifact.id,
        reviewer_run_id="reviewer",
        verdict=ReviewVerdict.APPROVED,
        reasons=["Aprobado por un modelo anterior."],
        acceptance_results={"Diseño listo": True},
    )
    dependency = WorkItem(
        id="dependency",
        project_id="project",
        milestone_id="milestone",
        title="Diseñar",
        description="Crear diseño",
        expected_outputs=["Diseño"],
        acceptance_criteria=["Diseño listo"],
    )
    repository = ContextRepository([artifact], [review], [dependency])
    builder = ContextBuilder(cast(Repository, repository), _capabilities())
    monkeypatch.setattr(builder, "project", lambda _project_id: {"brief": {}})
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear juego",
        description="Implementar lógica",
        expected_outputs=["Código"],
        dependency_ids=["dependency"],
        acceptance_criteria=["Juego funcional"],
    )

    context = builder.operational(item)

    assert context["dependency_artifacts"] == []
    assert context["dependency_references"] == []


def test_dependency_artifacts_includes_approved_work_with_empty_addressed_list() -> None:
    artifact = Artifact(
        id="approved-no-addressed",
        project_id="project",
        work_item_id="dependency",
        agent_run_id="worker",
        artifact_type="Design",
        title="Entrega aprobada sin acceptance_criteria_addressed",
        content={
            "files": [],
            "acceptance_criteria_addressed": [],
        },
    )
    review = Review(
        project_id="project",
        work_item_id="dependency",
        artifact_id=artifact.id,
        reviewer_run_id="reviewer",
        verdict=ReviewVerdict.APPROVED,
        reasons=["Aprobado por el revisor."],
        acceptance_results={"Diseño listo": True},
    )
    dependency = WorkItem(
        id="dependency",
        project_id="project",
        milestone_id="milestone",
        title="Diseñar",
        description="Crear diseño",
        expected_outputs=["Diseño"],
        acceptance_criteria=["Diseño listo"],
    )
    repository = ContextRepository([artifact], [review], [dependency])
    builder = ContextBuilder(cast(Repository, repository), _capabilities())
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear juego",
        description="Implementar lógica",
        expected_outputs=["Código"],
        dependency_ids=["dependency"],
        acceptance_criteria=["Juego funcional"],
    )

    dependency_artifacts = builder.dependency_artifacts(item)

    assert [entry["id"] for entry in dependency_artifacts] == ["approved-no-addressed"]


def test_operational_context_carries_the_runtime_capability_manifest(
    monkeypatch: Any,
) -> None:
    repository = ContextRepository([], [], [])
    capabilities = _capabilities()
    builder = ContextBuilder(cast(Repository, repository), capabilities)
    monkeypatch.setattr(builder, "project", lambda _project_id: {"brief": {}})
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear herramienta",
        description="Implementar lógica",
        expected_outputs=["Código"],
        acceptance_criteria=["Funciona"],
    )

    context = builder.operational(item)

    assert context["runtime_capabilities"] == capabilities.model_dump(mode="json")


def test_operational_context_surfaces_cumulative_rejected_imports_from_prior_events(
    monkeypatch: Any,
) -> None:
    repository = ContextRepository(
        [],
        [],
        [],
        events=[
            {
                "work_item_id": "current",
                "action": "unsupported_capability_detected",
                "metadata": {"rejected_imports": ["flask"]},
            }
        ],
    )
    builder = ContextBuilder(cast(Repository, repository), _capabilities())
    monkeypatch.setattr(builder, "project", lambda _project_id: {"brief": {}})
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear API",
        description="Implementar lógica",
        expected_outputs=["Código"],
        acceptance_criteria=["Funciona"],
    )

    context = builder.operational(item)

    assert context["retry_guidance"]["cumulative_rejected_imports"] == ["flask"]


def test_operational_context_dedupes_rejected_imports_across_attempts(
    monkeypatch: Any,
) -> None:
    repository = ContextRepository(
        [],
        [],
        [],
        events=[
            {
                "work_item_id": "current",
                "action": "unsupported_capability_detected",
                "metadata": {"rejected_imports": ["flask"]},
            },
            {
                "work_item_id": "current",
                "action": "unsupported_capability_detected",
                "metadata": {"rejected_imports": ["flask", "requests"]},
            },
            {
                "work_item_id": "other",
                "action": "unsupported_capability_detected",
                "metadata": {"rejected_imports": ["pandas"]},
            },
        ],
    )
    builder = ContextBuilder(cast(Repository, repository), _capabilities())
    monkeypatch.setattr(builder, "project", lambda _project_id: {"brief": {}})
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Crear API",
        description="Implementar lógica",
        expected_outputs=["Código"],
        acceptance_criteria=["Funciona"],
    )

    context = builder.operational(item)

    assert context["retry_guidance"]["cumulative_rejected_imports"] == [
        "flask",
        "requests",
    ]
