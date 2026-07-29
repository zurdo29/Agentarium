from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agentarium.domain.enums import AgentRole
from agentarium.domain.models import AgentDefinition, ProjectBrief
from agentarium.llm import (
    PLANNING_PROMPT_VERSION,
    WORKSPACE_PROMPT_VERSION,
    ModelRequest,
    render_prompt,
)
from agentarium.llm.mock import MockProvider
from agentarium.orchestration.engine import InvalidPlan, Orchestrator
from agentarium.planning import BriefProposal, PlanProposal

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "planning_cases.json").read_text(encoding="utf-8")
)


def _agent(role: AgentRole) -> AgentDefinition:
    return AgentDefinition(
        role=role,
        provider="mock",
        model=f"fixture-{role.value}",
        temperature=0,
        context_limit=8192,
        output_token_limit=2400,
        timeout_seconds=30,
        max_retries=0,
        tools=[],
        allowed_directories=[],
        authority="planning",
        execution_budget=3,
    )


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: str(case["name"]))
@pytest.mark.asyncio
async def test_planning_fixtures_satisfy_versioned_contract(case: dict[str, Any]) -> None:
    provider = MockProvider()
    brief_request = ModelRequest(
        operation="brief",
        project_id="fixture-project",
        payload={"goal": case["goal"], "title": case["name"]},
    )
    rendered = render_prompt(brief_request, _agent(AgentRole.DIRECTOR))

    assert f"AGENTARIUM_PROMPT_VERSION={PLANNING_PROMPT_VERSION}" in rendered
    assert case["goal"] in rendered
    assert "CONTRATO_JSON_SCHEMA=" in rendered
    assert "success_criteria" in rendered

    brief_response = await provider.generate(brief_request, _agent(AgentRole.DIRECTOR))
    brief = BriefProposal.model_validate(brief_response.content)
    assert case["goal"] in brief.summary

    plan_request = ModelRequest(
        operation="plan",
        project_id="fixture-project",
        payload={"brief": brief.model_dump(mode="json")},
    )
    plan_rendered = render_prompt(plan_request, _agent(AgentRole.TECHNICAL_MANAGER))
    assert '"dependencies"' in plan_rendered
    assert '"acceptance_criteria"' in plan_rendered
    assert "ninguna aclaración puede sustituir esa cobertura" in plan_rendered

    plan_response = await provider.generate(
        plan_request,
        _agent(AgentRole.TECHNICAL_MANAGER),
    )
    plan = PlanProposal.model_validate(plan_response.content)
    keys = [task.key for task in plan.tasks]
    depended_on = {dependency for task in plan.tasks for dependency in task.dependencies}

    assert keys == case["expected_task_keys"]
    assert [task.key for task in plan.tasks if not task.dependencies] == case["expected_root_keys"]
    assert [task.key for task in plan.tasks if task.key not in depended_on] == case[
        "expected_terminal_keys"
    ]
    assert all(task.acceptance_criteria for task in plan.tasks)
    assert set(brief.deliverables).issubset(
        {
            output
            for task in plan.tasks
            for output in task.expected_outputs
        }
    )
    assert set(brief.success_criteria).issubset(
        {
            criterion
            for task in plan.tasks
            for criterion in task.acceptance_criteria
        }
    )
    assert set(brief.scope).issubset(
        {
            criterion
            for task in plan.tasks
            for criterion in task.acceptance_criteria
        }
    )


def test_uncovered_brief_gets_a_terminal_delivery_contract() -> None:
    brief = ProjectBrief(
        project_id="fixture-project",
        summary="Construir una aplicación local completa.",
        scope=["Aplicación web local"],
        deliverables=["Aplicación funcional", "Guía de uso"],
        success_criteria=[
            "Permite crear, editar y eliminar registros",
            "Persiste los cambios localmente",
        ],
    )
    incomplete_plan = PlanProposal.model_validate(
        {
            "milestone": {
                "title": "Aclaración",
                "description": "Resolver una duda menor.",
            },
            "tasks": [
                {
                    "key": "clarify",
                    "title": "Definir un término",
                    "description": "Documentar el significado de un término.",
                    "dependencies": [],
                    "expected_outputs": ["Documento aclaratorio"],
                    "acceptance_criteria": ["El término queda definido"],
                    "risk": "low",
                    "priority": 100,
                }
            ],
        }
    )

    completed, missing_deliverables, missing_scope, missing_criteria = (
        Orchestrator._ensure_plan_covers_brief(incomplete_plan, brief)
    )

    assert missing_deliverables == brief.deliverables
    assert missing_scope == brief.scope
    assert missing_criteria == brief.success_criteria
    assert len(completed.tasks) == 2
    closing_task = completed.tasks[-1]
    assert closing_task.dependencies == ["clarify"]
    assert closing_task.expected_outputs == brief.deliverables
    assert closing_task.acceptance_criteria == [
        *brief.scope,
        *brief.success_criteria,
    ]


def test_complete_plan_is_not_augmented() -> None:
    brief = ProjectBrief(
        project_id="fixture-project",
        summary="Entregar un resultado.",
        scope=["Resultado local"],
        deliverables=["Artefacto final"],
        success_criteria=["El artefacto es verificable"],
    )
    plan = PlanProposal.model_validate(
        {
            "milestone": {
                "title": "Entrega",
                "description": "Crear la entrega.",
            },
            "tasks": [
                {
                    "key": "deliver",
                    "title": "Entregar",
                    "description": "Crear y verificar el artefacto.",
                    "dependencies": [],
                    "expected_outputs": brief.deliverables,
                    "acceptance_criteria": [
                        *brief.scope,
                        *brief.success_criteria,
                    ],
                    "risk": "low",
                    "priority": 100,
                }
            ],
        }
    )

    completed, missing_deliverables, missing_scope, missing_criteria = (
        Orchestrator._ensure_plan_covers_brief(plan, brief)
    )

    assert completed == plan
    assert missing_deliverables == []
    assert missing_scope == []
    assert missing_criteria == []


def test_invalid_planning_fixture_is_rejected_before_persistence() -> None:
    invalid = {
        "milestone": {"title": "Cyclic", "description": "Invalid graph"},
        "tasks": [
            {
                "key": "first",
                "title": "First",
                "description": "First task",
                "dependencies": ["second"],
                "expected_outputs": ["first"],
                "acceptance_criteria": ["observable"],
                "risk": "low",
                "priority": 50,
            },
            {
                "key": "second",
                "title": "Second",
                "description": "Second task",
                "dependencies": ["first"],
                "expected_outputs": ["second"],
                "acceptance_criteria": ["observable"],
                "risk": "low",
                "priority": 40,
            },
        ],
    }

    with pytest.raises(InvalidPlan, match="cycle"):
        Orchestrator._validate_plan(invalid)


def test_work_prompt_exposes_versioned_workspace_contract() -> None:
    request = ModelRequest(
        operation="work",
        project_id="fixture-project",
        work_item_id="fixture-task",
        payload={
            "task": {
                "title": "Materialize",
                "description": "Write a bounded file",
            }
        },
    )

    rendered = render_prompt(request, _agent(AgentRole.IMPLEMENTATION_WORKER))

    assert f"AGENTARIUM_PROMPT_VERSION={WORKSPACE_PROMPT_VERSION}" in rendered
    assert '"files"' in rendered
    assert '"path"' in rendered
    assert '"content"' in rendered
    assert '"purpose"' in rendered
    assert "placeholders" in rendered
    assert "funciones vacías" in rendered


@pytest.mark.parametrize(
    ("operation", "role", "required_fields"),
    [
        ("test", AgentRole.TESTER, ['"passed"', '"checks"', '"summary"']),
        (
            "review",
            AgentRole.CRITICAL_REVIEWER,
            ['"verdict"', '"reasons"', '"acceptance_results"'],
        ),
    ],
)
def test_evaluation_prompts_include_strict_contracts(
    operation: str,
    role: AgentRole,
    required_fields: list[str],
) -> None:
    request = ModelRequest(
        operation=operation,
        project_id="fixture-project",
        work_item_id="fixture-task",
        payload={"acceptance_criteria": ["El resultado funciona"]},
    )

    rendered = render_prompt(request, _agent(role))

    assert "CONTRATO_JSON_SCHEMA=" in rendered
    assert all(field in rendered for field in required_fields)
