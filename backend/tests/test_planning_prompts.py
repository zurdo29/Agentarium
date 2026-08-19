from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agentarium.domain.enums import AgentRole, OutputStrategy
from agentarium.domain.models import AgentDefinition, ProjectBrief
from agentarium.llm import (
    PLANNING_PROMPT_VERSION,
    WORKSPACE_PROMPT_VERSION,
    ModelRequest,
    ProviderResponse,
    render_prompt,
)
from agentarium.llm.mock import MockProvider
from agentarium.orchestration.engine import InvalidPlan, Orchestrator
from agentarium.planning import (
    BriefProposal,
    DecomposeProposal,
    PlanProposal,
    SubtaskProposal,
    implicit_path_claims,
    merge_path_claims,
)
from agentarium.services import ApplicationService
from pydantic import ValidationError

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
    assert "project.decisions es un registro interno" in rendered
    assert "SOLICITUD.payload.runtime_capabilities" in rendered
    assert "third_party_packages_allowed" in rendered
    assert "biblioteca estándar de Python" in rendered


@pytest.mark.asyncio
async def test_plan_payload_carries_the_runtime_capability_manifest(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real build_application() wiring end to end, not a direct render_prompt
    # call: confirms the same source manifest instance built at startup is
    # actually the one serialized into the "plan" operation's payload.
    original_generate = MockProvider.generate
    captured: list[dict[str, Any]] = []

    async def capture_plan_payload(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "plan":
            captured.append(dict(request.payload))
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", capture_plan_payload)

    project = service.create_project("Entregar un resultado verificable")
    await service.orchestrator.plan_project(project.id)

    assert captured, "la operacion plan nunca fue invocada"
    manifest = captured[0]["runtime_capabilities"]
    assert manifest["network_policy"] == "deny"
    assert manifest["third_party_packages_allowed"] == []
    assert manifest["executables_allowed"]


@pytest.mark.asyncio
async def test_plan_revision_payload_carries_the_same_manifest_instance(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate
    captured_plan: list[dict[str, Any]] = []
    captured_revision: list[dict[str, Any]] = []

    async def colliding_then_capture(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "plan":
            captured_plan.append(dict(request.payload))
            content = {
                "milestone": {
                    "title": "MVP verificable",
                    "description": "Del objetivo a un resultado integrado.",
                },
                "tasks": [
                    {
                        "key": "task_a",
                        "title": "Tarea A",
                        "description": "Primera mitad del módulo compartido.",
                        "dependencies": [],
                        "expected_outputs": ["parte a"],
                        "acceptance_criteria": ["Existe la parte a"],
                        "risk": "low",
                        "priority": 90,
                        "owned_paths": ["shared.py"],
                    },
                    {
                        "key": "task_b",
                        "title": "Tarea B",
                        "description": "Segunda mitad del módulo compartido.",
                        "dependencies": [],
                        "expected_outputs": ["parte b"],
                        "acceptance_criteria": ["Existe la parte b"],
                        "risk": "low",
                        "priority": 90,
                        "owned_paths": ["shared.py"],
                    },
                ],
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        if request.operation == "plan_revision":
            captured_revision.append(dict(request.payload))
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", colliding_then_capture)

    project = service.create_project("Objetivo con conflicto de propiedad de archivo")
    await service.orchestrator.plan_project(project.id)

    assert captured_plan, "la operacion plan nunca fue invocada"
    assert captured_revision, "la operacion plan_revision nunca fue invocada"
    # Same source instance serialized into both payloads, not two
    # independently-built copies that could drift.
    assert (
        captured_plan[0]["runtime_capabilities"]
        == captured_revision[0]["runtime_capabilities"]
    )


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


def _subtask(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Subtarea",
        "description": "Descripción",
        "expected_outputs": ["resultado"],
        "acceptance_criteria": ["Existe"],
        "owned_paths": [],
        "shared_component": None,
        "output_strategy": "exclusive",
    }
    base.update(overrides)
    return base


def test_owned_paths_rejects_absolute_and_escaping_entries() -> None:
    with pytest.raises(ValidationError):
        SubtaskProposal.model_validate(_subtask(owned_paths=["/etc/passwd"]))
    with pytest.raises(ValidationError):
        SubtaskProposal.model_validate(_subtask(owned_paths=["../outside.py"]))
    with pytest.raises(ValidationError):
        SubtaskProposal.model_validate(_subtask(owned_paths=["  "]))


def test_owned_paths_defaults_to_empty_and_accepts_relative_paths() -> None:
    subtask = SubtaskProposal.model_validate(_subtask())
    assert subtask.owned_paths == []
    assert subtask.output_strategy is OutputStrategy.EXCLUSIVE

    subtask = SubtaskProposal.model_validate(
        _subtask(owned_paths=["routes/books.py"])
    )
    assert subtask.owned_paths == ["routes/books.py"]


def test_implicit_path_claims_only_counts_file_like_entries() -> None:
    assert implicit_path_claims(
        [
            "api.py",
            "docs/design.md",
            "src\\routes\\books.ts",
        ]
    ) == ["api.py", "docs/design.md", "src/routes/books.ts"]

    # Prose, versions and unsafe paths are not ownership claims.
    assert (
        implicit_path_claims(
            [
                "resultado",
                "Documento de arquitectura en Markdown",
                "Una API REST funcionando",
                "informe final.md",
                "v1.2",
                "/etc/passwd",
                "../outside.py",
                "C:/tmp/out.py",
            ]
        )
        == []
    )


# Gate-MVP.3 follow-up (ADR 0042). The exact strings the planner produced in
# `benchmarks/results/gate-mvp3-textkit-slugify-2026-08/`, read from the run's
# own database -- not paraphrased. The original measurement produced bare
# paths for the same goal and model; this run produced prose, and that alone
# decided whether the ownership boundary armed at all.


def test_implicit_path_claims_reads_the_exact_prose_gate_mvp3_produced() -> None:
    assert implicit_path_claims(
        ["Código modificado en `textkit/slug.py`"]
    ) == ["textkit/slug.py"]
    assert implicit_path_claims(
        ["Nueva prueba en `tests/test_slug.py`"]
    ) == ["tests/test_slug.py"]
    assert implicit_path_claims(
        ["Nueva prueba en `tests/test_slug.py` que cubra el ejemplo dado"]
    ) == ["tests/test_slug.py"]


def test_implicit_path_claims_ignores_dotted_identifiers_inside_backticks() -> None:
    """`re.sub`, `str.strip` and `os.path` all match `_PATH_CLAIM_PATTERN`
    (verified against the real pattern). Claiming them would arm an ownership
    boundary with a path that does not exist and reject the task's own
    legitimate delivery -- strictly worse than detecting nothing."""
    assert (
        implicit_path_claims(
            [
                "Usar `re.sub` para limpiar el texto",
                "Llamar `str.strip` antes de comparar",
                "Resolver con `os.path`",
            ]
        )
        == []
    )


def test_implicit_path_claims_accepts_a_separator_less_file_inside_backticks() -> None:
    assert implicit_path_claims(["Entregar `INFORME.md` al final"]) == ["INFORME.md"]
    assert implicit_path_claims(["Ajustar `config.yaml` del proyecto"]) == [
        "config.yaml"
    ]


def test_implicit_path_claims_rejects_unsafe_paths_inside_backticks() -> None:
    assert (
        implicit_path_claims(
            [
                "Leer `/etc/passwd`",
                "Tocar `../outside.py`",
                "Abrir `C:/tmp/out.py`",
            ]
        )
        == []
    )


def test_implicit_path_claims_leaves_entries_without_backticks_unchanged() -> None:
    """Regression of the pre-existing contract: the whole-entry rule keeps its
    original, looser bar and its original results."""
    assert implicit_path_claims(["api.py", "docs/design.md"]) == [
        "api.py",
        "docs/design.md",
    ]
    assert (
        implicit_path_claims(
            ["resultado", "Documento de arquitectura en Markdown", "informe final.md"]
        )
        == []
    )


def test_implicit_path_claims_deduplicates_across_both_spellings() -> None:
    assert implicit_path_claims(
        ["textkit/slug.py", "Código modificado en `textkit/slug.py`"]
    ) == ["textkit/slug.py"]


def test_implicit_path_claims_reads_several_backticked_paths_in_one_entry() -> None:
    assert implicit_path_claims(
        ["Tocar `textkit/slug.py` y también `tests/test_slug.py`"]
    ) == ["textkit/slug.py", "tests/test_slug.py"]


def test_merge_path_claims_keeps_declared_paths_and_adds_implicit_ones() -> None:
    assert merge_path_claims(["routes/books.py"], ["api.py", "resultado"]) == [
        "routes/books.py",
        "api.py",
    ]
    # An implicit claim already declared explicitly is not duplicated.
    assert merge_path_claims(["Api.py"], ["api.py"]) == ["Api.py"]


def test_decompose_proposal_rejects_overlap_declared_only_via_expected_outputs() -> None:
    # The exact shape qwen2.5-coder:7b produced in workspace 487c5194: the file
    # is named in expected_outputs, owned_paths stays empty, strategy is the
    # default "exclusive".
    with pytest.raises(ValidationError, match="path claim overlap"):
        DecomposeProposal.model_validate(
            {
                "subtasks": [
                    _subtask(title="Listar", expected_outputs=["api.py"]),
                    _subtask(title="Agregar", expected_outputs=["api.py"]),
                ]
            }
        )


def test_decompose_proposal_allows_distinct_expected_output_files() -> None:
    proposal = DecomposeProposal.model_validate(
        {
            "subtasks": [
                _subtask(title="Listar", expected_outputs=["routes/list.py"]),
                _subtask(title="Agregar", expected_outputs=["routes/create.py"]),
            ]
        }
    )
    assert len(proposal.subtasks) == 2


def test_decompose_proposal_rejects_overlap_without_grouping() -> None:
    with pytest.raises(ValidationError, match="path claim overlap"):
        DecomposeProposal.model_validate(
            {
                "subtasks": [
                    _subtask(title="Parte 1", owned_paths=["library_api.py"]),
                    _subtask(title="Parte 2", owned_paths=["library_api.py"]),
                ]
            }
        )


def test_decompose_proposal_allows_overlap_with_matching_shared_component() -> None:
    proposal = DecomposeProposal.model_validate(
        {
            "subtasks": [
                _subtask(
                    title="Parte 1",
                    owned_paths=["library_api.py"],
                    shared_component="library_api",
                    output_strategy="fragment",
                ),
                _subtask(
                    title="Parte 2",
                    owned_paths=["library_api.py"],
                    shared_component="library_api",
                    output_strategy="consolidation",
                ),
            ]
        }
    )
    assert len(proposal.subtasks) == 2


@pytest.mark.asyncio
async def test_plan_owned_path_conflicts_trigger_a_revision(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    async def colliding_plan(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "plan":
            content = {
                "milestone": {
                    "title": "MVP verificable",
                    "description": "Del objetivo a un resultado integrado.",
                },
                "tasks": [
                    {
                        "key": "task_a",
                        "title": "Tarea A",
                        "description": "Primera mitad del módulo compartido.",
                        "dependencies": [],
                        "expected_outputs": ["parte a"],
                        "acceptance_criteria": ["Existe la parte a"],
                        "risk": "low",
                        "priority": 90,
                        "owned_paths": ["shared.py"],
                    },
                    {
                        "key": "task_b",
                        "title": "Tarea B",
                        "description": "Segunda mitad del módulo compartido.",
                        "dependencies": [],
                        "expected_outputs": ["parte b"],
                        "acceptance_criteria": ["Existe la parte b"],
                        "risk": "low",
                        "priority": 90,
                        "owned_paths": ["shared.py"],
                    },
                ],
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", colliding_plan)

    project = service.create_project("Objetivo con conflicto de propiedad de archivo")
    await service.orchestrator.plan_project(project.id)

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "plan_owned_path_conflict_detected" in events
    assert "plan_owned_path_conflict_unresolved" not in events

    items = {
        item.title: item
        for item in service.repository.list_work_items(project.id)
    }
    task_a = items["Tarea A"]
    task_b = items["Tarea B"]
    assert task_a.shared_component is not None
    assert task_a.shared_component == task_b.shared_component
    assert task_a.output_strategy is not OutputStrategy.EXCLUSIVE
    assert task_b.output_strategy is not OutputStrategy.EXCLUSIVE


@pytest.mark.asyncio
async def test_plan_conflicts_are_detected_from_expected_outputs_alone(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    def _task(key: str, title: str, output: str) -> dict[str, object]:
        # owned_paths deliberately absent: the model names the file it will
        # write in expected_outputs and never adopts the newer field.
        return {
            "key": key,
            "title": title,
            "description": f"Endpoints de {title}.",
            "dependencies": [],
            "expected_outputs": [output],
            "acceptance_criteria": [f"Responde a solicitudes de {title}"],
            "risk": "low",
            "priority": 90,
        }

    async def colliding_plan(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "plan":
            content = {
                "milestone": {
                    "title": "MVP verificable",
                    "description": "Del objetivo a un resultado integrado.",
                },
                "tasks": [
                    _task("task_a", "Listar", "api.py"),
                    _task("task_b", "Agregar", "api.py"),
                    _task("task_c", "Documentar", "README.md"),
                ],
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", colliding_plan)

    project = service.create_project("Objetivo con archivo compartido implícito")
    await service.orchestrator.plan_project(project.id)

    conflict_events = [
        event
        for event in service.repository.list_events(project.id)
        if event["action"] == "plan_owned_path_conflict_detected"
    ]
    assert conflict_events
    conflicts = conflict_events[0]["metadata"]["conflicts"]
    assert [conflict["paths"] for conflict in conflicts] == [["api.py"]]
    assert conflicts[0]["declared_via"] == ["expected_outputs"]
    assert {conflicts[0]["task_a"], conflicts[0]["task_b"]} == {"task_a", "task_b"}

    items = {
        item.title: item
        for item in service.repository.list_work_items(project.id)
    }
    assert items["Listar"].shared_component == items["Agregar"].shared_component
    assert items["Listar"].output_strategy is not OutputStrategy.EXCLUSIVE
    assert items["Agregar"].output_strategy is not OutputStrategy.EXCLUSIVE
    # The task nobody collides with is left alone.
    assert items["Documentar"].shared_component is None
    assert items["Documentar"].output_strategy is OutputStrategy.EXCLUSIVE
