from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import ProjectStatus, ReviewVerdict, RiskLevel, WorkItemStatus
from agentarium.domain.models import WorkItem
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.orchestration.engine import Orchestrator
from agentarium.services import ApplicationService

# --- the pure derivation function, in isolation -----------------------------


def test_single_expected_output_generates_one_criterion() -> None:
    result = Orchestrator._with_expected_output_criteria(["README.md"], [])
    assert result == ["El entregable esperado existe y está completo: README.md"]


def test_multiple_expected_outputs_generate_one_criterion_each() -> None:
    result = Orchestrator._with_expected_output_criteria(["README.md", "api.py"], [])
    assert result == [
        "El entregable esperado existe y está completo: README.md",
        "El entregable esperado existe y está completo: api.py",
    ]


def test_duplicate_expected_outputs_do_not_duplicate_the_criterion() -> None:
    result = Orchestrator._with_expected_output_criteria(
        ["README.md", "README.md"], []
    )
    assert result == ["El entregable esperado existe y está completo: README.md"]


def test_prose_style_expected_output_is_handled_like_any_other_string() -> None:
    prose = "Documentación completa de la API con ejemplos de uso"
    result = Orchestrator._with_expected_output_criteria([prose], [])
    assert result == [f"El entregable esperado existe y está completo: {prose}"]


def test_existing_model_criteria_are_preserved_alongside_derived_ones() -> None:
    result = Orchestrator._with_expected_output_criteria(
        ["README.md"], ["El código sigue el estilo del proyecto"]
    )
    assert result == [
        "El código sigue el estilo del proyecto",
        "El entregable esperado existe y está completo: README.md",
    ]


def test_does_not_try_to_guess_that_another_criterion_already_covers_it() -> None:
    # No fuzzy matching: an existing criterion that mentions the deliverable in
    # free text does not stop the deterministic one from being added too.
    result = Orchestrator._with_expected_output_criteria(
        ["README.md"],
        ["El README.md debe explicar cómo instalar el proyecto"],
    )
    assert result == [
        "El README.md debe explicar cómo instalar el proyecto",
        "El entregable esperado existe y está completo: README.md",
    ]


def test_applying_twice_is_idempotent() -> None:
    once = Orchestrator._with_expected_output_criteria(["README.md", "api.py"], [])
    twice = Orchestrator._with_expected_output_criteria(["README.md", "api.py"], once)
    assert twice == once


# --- reaches the initial plan (integration, mock provider) -----------------


@pytest.mark.asyncio
async def test_initial_plan_tasks_get_a_derived_criterion_per_expected_output(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.orchestrator.plan_project(project.id)

    items = service.repository.list_work_items(project.id)
    scope = next(
        item for item in items if item.title == "Especificar el alcance verificable"
    )

    assert scope.expected_outputs == ["specification"]
    assert (
        "El entregable esperado existe y está completo: specification"
        in scope.acceptance_criteria
    )
    # The model's own criteria survive alongside the derived one.
    assert "Incluye alcance y exclusiones" in scope.acceptance_criteria
    assert "Define evidencia verificable" in scope.acceptance_criteria

    integration = next(
        item for item in items if item.title == "Integrar y documentar el resultado"
    )
    for output in integration.expected_outputs:
        assert (
            f"El entregable esperado existe y está completo: {output}"
            in integration.acceptance_criteria
        )


# --- reaches the reviewer, and a false result blocks approval --------------


@pytest.mark.asyncio
async def test_derived_criterion_reaches_the_reviewer_and_a_false_result_blocks_approval(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.orchestrator.plan_project(project.id)
    milestone_id = service.repository.list_work_items(project.id)[0].milestone_id

    # A single-criterion, single-output task, independent of the standard
    # plan's tasks: exactly the derivation this PR adds, with nothing else to
    # dilute the result and too few criteria to become split-eligible.
    criteria = Orchestrator._with_expected_output_criteria(["INFORME.md"], [])
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone_id,
        title="Entregar el informe final",
        description="Entregar únicamente el informe final del proyecto",
        expected_outputs=["INFORME.md"],
        acceptance_criteria=criteria,
        max_attempts=1,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)

    seen_payload_criteria: list[list[str]] = []
    original_generate = MockProvider.generate

    async def reject_this_task(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id == item.id:
            # Gate-MVP.1 (ADR 0040): MockProvider's own path-derivation
            # fallback (llm/mock.py, artifact_type not in its label table)
            # produces "deliverables/INFORME.md.md", which does not match
            # this item's own expected_outputs=["INFORME.md"] and would get
            # rejected before ever reaching the review step below -- fix the
            # delivered path so this test still exercises what it names.
            content = {
                "artifact_type": "Documento",
                "title": "Informe final",
                "summary": "Entrega determinista para la prueba.",
                "quality": "verified",
                "files": [
                    {
                        "path": "INFORME.md",
                        "content": "# Informe final\n",
                        "purpose": "Informe final del proyecto",
                    }
                ],
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        if request.operation == "review" and request.work_item_id == item.id:
            payload_criteria = list(request.payload["acceptance_criteria"])
            seen_payload_criteria.append(payload_criteria)
            content = {
                "verdict": "changes_requested",
                "reasons": ["El entregable declarado no está presente"],
                "acceptance_results": {c: False for c in payload_criteria},
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", reject_this_task)

    result = await service.run_project(project.id)

    # It actually reached the reviewer: the exact derived criterion was part
    # of the payload sent for evaluation, not just present on the WorkItem.
    assert seen_payload_criteria, "el revisor nunca fue invocado para esta tarea"
    assert criteria[0] in seen_payload_criteria[0]

    # A false result on it mechanically blocks approval of this task — the
    # project as a whole cannot silently complete around it.
    assert result.status is not ProjectStatus.COMPLETED
    updated = service.repository.get_work_item(item.id)
    assert updated.status is not WorkItemStatus.COMPLETED

    reviews = [
        review
        for review in service.repository.list_reviews(project.id)
        if review.work_item_id == item.id
    ]
    assert reviews
    assert reviews[-1].verdict is ReviewVerdict.CHANGES_REQUESTED
    assert reviews[-1].acceptance_results[criteria[0]] is False
