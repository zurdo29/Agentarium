from __future__ import annotations

import pytest
from agentarium.domain.enums import AgentRole, OutputStrategy, RunOutcome
from agentarium.domain.models import AgentRun, Artifact, Milestone, ResourceUsage, WorkItem
from agentarium.execution import ReviewEvaluationProposal, WorkArtifactProposal
from agentarium.orchestration.engine import InvalidPlan, Orchestrator
from agentarium.services import ApplicationService


def test_tester_contract_rejects_renamed_fields() -> None:
    with pytest.raises(InvalidPlan, match="tester evaluation"):
        Orchestrator._validate_test(
            {
                "success": True,
                "validations": [],
                "message": "Looks good",
            }
        )


def test_reviewer_must_cover_every_acceptance_criterion() -> None:
    with pytest.raises(InvalidPlan, match="every acceptance criterion"):
        Orchestrator._validate_review(
            {
                "verdict": "approved",
                "reasons": ["La evidencia es suficiente."],
                "acceptance_results": {"Primer criterio": True},
            },
            ["Primer criterio", "Segundo criterio"],
        )


def test_failed_technical_gate_bounds_semantic_review_scope() -> None:
    criteria = [f"Criterio {index}" for index in range(10)]

    assert Orchestrator._semantic_review_criteria(
        criteria,
        report_passed=False,
    ) == criteria[:3]
    assert Orchestrator._semantic_review_criteria(
        criteria,
        report_passed=True,
    ) == criteria


def test_identical_retry_candidate_is_detected_from_file_content() -> None:
    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "code",
            "title": "Aplicación",
            "summary": "Entrega corregida.",
            "quality": "verified",
            "files": [
                {
                    "path": "app.js",
                    "content": "const answer = 42;",
                    "purpose": "Lógica",
                }
            ],
        }
    )
    artifact = Artifact(
        project_id="project",
        work_item_id="task",
        agent_run_id="run",
        artifact_type="code",
        title="Intento anterior",
        content=proposal.model_dump(mode="json"),
    )

    assert Orchestrator._candidate_matches_artifact(proposal, artifact)

    changed = proposal.model_copy(deep=True)
    changed.files[0].content = "const answer = 43;"

    assert not Orchestrator._candidate_matches_artifact(changed, artifact)


def test_colliding_dependency_paths_detects_unrelated_task_reusing_a_path(
    service: ApplicationService,
) -> None:
    project = service.create_project("Detectar colisión de rutas entre tareas")
    milestone = Milestone(
        project_id=project.id,
        title="Hito",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)

    owner = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea A",
        description="Escribe el glosario",
        expected_outputs=["glosario"],
        acceptance_criteria=["Existe"],
    )
    service.repository.add_work_item(owner)
    run = AgentRun(
        project_id=project.id,
        work_item_id=owner.id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="mock",
        provider="mock",
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="s",
        output_summary="s",
        resource_usage=ResourceUsage(model="mock", provider="mock"),
        correlation_id="corr-a",
    )
    service.repository.add_agent_run(run)
    service.repository.add_artifact(
        Artifact(
            project_id=project.id,
            work_item_id=owner.id,
            agent_run_id=run.id,
            artifact_type="Doc",
            title="Entrega A",
            content={
                "files": [
                    {"path": "docs/design.md", "content": "A", "purpose": "p"}
                ],
            },
        )
    )

    unrelated = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea B",
        description="Escribe las decisiones de diseño",
        expected_outputs=["decisiones"],
        acceptance_criteria=["Existe"],
    )
    service.repository.add_work_item(unrelated)

    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "Doc",
            "title": "Entrega B",
            "summary": "s",
            "quality": "verified",
            "files": [
                {"path": "docs/design.md", "content": "B", "purpose": "p"}
            ],
        }
    )

    colliding = service.orchestrator._colliding_dependency_paths(unrelated, proposal)
    assert colliding == {"docs/design.md"}

    dependent = unrelated.model_copy(update={"dependency_ids": [owner.id]})
    assert (
        service.orchestrator._colliding_dependency_paths(dependent, proposal) == set()
    )


def test_colliding_dependency_paths_allows_transitive_ancestor_ownership(
    service: ApplicationService,
) -> None:
    project = service.create_project("Cierre depende transitivamente del dueño")
    milestone = Milestone(
        project_id=project.id,
        title="Hito",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)

    grandparent = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Componentes",
        description="Identifica componentes",
        expected_outputs=["componentes"],
        acceptance_criteria=["Existe"],
    )
    service.repository.add_work_item(grandparent)
    run = AgentRun(
        project_id=project.id,
        work_item_id=grandparent.id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="mock",
        provider="mock",
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="s",
        output_summary="s",
        resource_usage=ResourceUsage(model="mock", provider="mock"),
        correlation_id="corr-grandparent",
    )
    service.repository.add_agent_run(run)
    service.repository.add_artifact(
        Artifact(
            project_id=project.id,
            work_item_id=grandparent.id,
            agent_run_id=run.id,
            artifact_type="Doc",
            title="Componentes",
            content={
                "files": [
                    {"path": "docs/architecture.md", "content": "A", "purpose": "p"}
                ],
            },
        )
    )

    parent = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Revisión",
        description="Revisa y aprueba",
        expected_outputs=["revision"],
        acceptance_criteria=["Existe"],
        dependency_ids=[grandparent.id],
    )
    service.repository.add_work_item(parent)

    closing = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Completar y verificar la entrega del proyecto",
        description="Consolida el resultado",
        expected_outputs=["documento final"],
        acceptance_criteria=["Existe"],
        dependency_ids=[parent.id],
    )

    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "Doc",
            "title": "Documento final",
            "summary": "s",
            "quality": "verified",
            "files": [
                {"path": "docs/architecture.md", "content": "final", "purpose": "p"}
            ],
        }
    )

    assert service.orchestrator._colliding_dependency_paths(closing, proposal) == set()


def test_colliding_dependency_paths_allows_matching_shared_component_fragment(
    service: ApplicationService,
) -> None:
    project = service.create_project("Fragmentos comparten componente")
    milestone = Milestone(
        project_id=project.id,
        title="Hito",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)

    owner = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea A",
        description="Escribe una parte del módulo compartido",
        expected_outputs=["modulo_parte_a"],
        acceptance_criteria=["Existe"],
        shared_component="library_api",
        output_strategy=OutputStrategy.FRAGMENT,
    )
    service.repository.add_work_item(owner)
    run = AgentRun(
        project_id=project.id,
        work_item_id=owner.id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="mock",
        provider="mock",
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="s",
        output_summary="s",
        resource_usage=ResourceUsage(model="mock", provider="mock"),
        correlation_id="corr-fragment-a",
    )
    service.repository.add_agent_run(run)
    service.repository.add_artifact(
        Artifact(
            project_id=project.id,
            work_item_id=owner.id,
            agent_run_id=run.id,
            artifact_type="Code",
            title="Entrega A",
            content={
                "files": [
                    {"path": "library_api.py", "content": "A", "purpose": "p"}
                ],
            },
        )
    )

    candidate = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea B",
        description="Escribe otra parte del módulo compartido",
        expected_outputs=["modulo_parte_b"],
        acceptance_criteria=["Existe"],
        shared_component="library_api",
        output_strategy=OutputStrategy.FRAGMENT,
    )
    service.repository.add_work_item(candidate)

    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "Code",
            "title": "Entrega B",
            "summary": "s",
            "quality": "verified",
            "files": [
                {"path": "library_api.py", "content": "B", "purpose": "p"}
            ],
        }
    )

    assert service.orchestrator._colliding_dependency_paths(candidate, proposal) == set()

    # Only the CANDIDATE's own strategy is exempted — an otherwise-identical
    # candidate that claims "exclusive" still collides, even with a matching
    # shared_component, because it isn't declaring itself as sharing.
    exclusive_candidate = candidate.model_copy(
        update={"output_strategy": OutputStrategy.EXCLUSIVE}
    )
    assert service.orchestrator._colliding_dependency_paths(
        exclusive_candidate, proposal
    ) == {"library_api.py"}


def _proposal(paths: list[str]) -> WorkArtifactProposal:
    return WorkArtifactProposal.model_validate(
        {
            "artifact_type": "code",
            "title": "Entrega",
            "summary": "s",
            "quality": "verified",
            "files": [
                {"path": path, "content": "contenido", "purpose": "p"}
                for path in paths
            ],
        }
    )


def test_out_of_scope_paths_rejects_a_file_outside_the_items_own_claims() -> None:
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Modificar la función `slugify`",
        description="Corrige el bug de guiones al borde",
        expected_outputs=["textkit/slug.py"],
        acceptance_criteria=["Existe"],
    )
    proposal = _proposal(["textkit/slug.py", "tests/test_slug.py"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == {"tests/test_slug.py"}


def test_out_of_scope_paths_is_permissive_without_any_parseable_claim() -> None:
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Tarea en prosa",
        description="Escribe el glosario",
        expected_outputs=["glosario"],
        acceptance_criteria=["Existe"],
    )
    proposal = _proposal(["docs/design.md"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == set()


def test_out_of_scope_paths_combines_owned_paths_and_expected_outputs() -> None:
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Tarea con dos claims",
        description="Entrega dos archivos declarados por dos vías distintas",
        expected_outputs=["b.py"],
        acceptance_criteria=["Existe"],
        owned_paths=["a.py"],
    )
    proposal = _proposal(["a.py", "b.py"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == set()


def test_out_of_scope_paths_normalizes_backslashes_and_case() -> None:
    # owned_paths declarado con backslash y mayúsculas; merge_path_claims lo
    # devuelve tal cual (sin normalizar) -- la normalización es responsabilidad
    # de _out_of_scope_paths, no de merge_path_claims.
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Tarea con claim declarado en formato distinto",
        description="owned_paths con separador y mayúsculas distintos al candidato",
        expected_outputs=["el caso de prueba"],
        acceptance_criteria=["Existe"],
        owned_paths=["Tests\\Test_Slug.PY"],
    )
    proposal = _proposal(["tests/test_slug.py"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == set()


def test_out_of_scope_paths_ignores_shared_component_without_an_own_claim() -> None:
    # shared_component ya NO exime nada acá (a diferencia de
    # _colliding_dependency_paths): esa excepción responde "¿pueden dos
    # tareas relacionadas compartir un archivo?", no "¿esta tarea se salió
    # de su propio scope?".
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Fragmento B",
        description="Escribe su propia parte del módulo compartido",
        expected_outputs=["library_api_part_b.py"],
        acceptance_criteria=["Existe"],
        shared_component="library_api",
        output_strategy=OutputStrategy.FRAGMENT,
    )
    # Claim real propio (library_api_part_b.py) + el path que "posee" la
    # hermana del fragmento (library_api.py) -- shared_component no debe
    # extender el claim propio a ese segundo path.
    proposal = _proposal(["library_api_part_b.py", "library_api.py"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == {"library_api.py"}


def test_out_of_scope_paths_allows_a_shared_path_when_the_item_declares_it_itself() -> None:
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Fragmento B",
        description="Escribe su propia parte, declarándola explícitamente",
        expected_outputs=["modulo_parte_b"],
        acceptance_criteria=["Existe"],
        owned_paths=["library_api.py"],
        shared_component="library_api",
        output_strategy=OutputStrategy.FRAGMENT,
    )
    proposal = _proposal(["library_api.py"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == set()


def test_out_of_scope_paths_ignores_dependency_ids_without_an_own_claim() -> None:
    # Tener un ancestro real tampoco exime nada acá -- a diferencia de
    # _colliding_dependency_paths, que sí puede dejar reescribir el path de
    # un ancestro. Esta compuerta sólo mira el scope propio declarado.
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Cierre",
        description="Consolida el resultado",
        expected_outputs=["docs/final.md"],
        acceptance_criteria=["Existe"],
        dependency_ids=["ancestor-id"],
    )
    proposal = _proposal(["docs/final.md", "docs/architecture.md"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == {"docs/architecture.md"}


def test_out_of_scope_paths_allows_a_path_when_the_item_declares_it_despite_dependencies() -> None:
    item = WorkItem(
        project_id="p",
        milestone_id="m",
        title="Cierre",
        description="Reescribe legítimamente el archivo del ancestro, declarado",
        expected_outputs=["docs/final.md"],
        acceptance_criteria=["Existe"],
        owned_paths=["docs/architecture.md"],
        dependency_ids=["ancestor-id"],
    )
    proposal = _proposal(["docs/final.md", "docs/architecture.md"])

    assert Orchestrator._out_of_scope_paths(item, proposal) == set()


def test_reject_out_of_scope_write_emits_event_and_raises(
    service: ApplicationService,
) -> None:
    project = service.create_project("Rechazo de escritura fuera de scope")
    milestone = Milestone(
        project_id=project.id,
        title="Hito",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Modificar la función `slugify`",
        description="Corrige el bug de guiones al borde",
        expected_outputs=["textkit/slug.py"],
        acceptance_criteria=["Existe"],
    )
    service.repository.add_work_item(item)
    proposal = _proposal(["textkit/slug.py", "tests/test_slug.py"])

    with pytest.raises(InvalidPlan, match="own declared scope"):
        service.orchestrator._reject_out_of_scope_write(item, proposal, "corr-1")

    events = service.repository.list_events_for_work_item(project.id, item.id)
    matching = [
        event for event in events if event["action"] == "workspace_own_scope_rejected"
    ]
    assert len(matching) == 1
    metadata = matching[0]["metadata"]
    assert metadata["paths"] == ["tests/test_slug.py"]
    assert metadata["candidate_paths"] == ["tests/test_slug.py", "textkit/slug.py"]
    assert metadata["claimed_paths"] == ["textkit/slug.py"]
    assert metadata["owned_paths"] == []
    assert metadata["expected_outputs"] == ["textkit/slug.py"]


def test_reject_out_of_scope_write_is_a_noop_when_everything_is_in_scope(
    service: ApplicationService,
) -> None:
    project = service.create_project("Sin violación de scope")
    milestone = Milestone(
        project_id=project.id,
        title="Hito",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Modificar la función `slugify`",
        description="Corrige el bug de guiones al borde",
        expected_outputs=["textkit/slug.py"],
        acceptance_criteria=["Existe"],
    )
    service.repository.add_work_item(item)
    proposal = _proposal(["textkit/slug.py"])

    service.orchestrator._reject_out_of_scope_write(item, proposal, "corr-2")

    events = service.repository.list_events_for_work_item(project.id, item.id)
    assert not any(
        event["action"] == "workspace_own_scope_rejected" for event in events
    )


@pytest.mark.parametrize(
    ("raw_verdict", "criterion_passed", "expected_verdict"),
    [
        ("changes_requested", True, "approved"),
        ("approved", False, "changes_requested"),
    ],
)
def test_reviewer_verdict_is_derived_from_acceptance_results(
    raw_verdict: str,
    criterion_passed: bool,
    expected_verdict: str,
) -> None:
    proposal = Orchestrator._validate_review(
        {
            "verdict": raw_verdict,
            "reasons": ["Una opinión fuera del alcance."],
            "acceptance_results": {"Criterio": criterion_passed},
        },
        ["Criterio"],
    )

    assert proposal.verdict == expected_verdict


def test_partial_review_fragments_are_merged_in_criterion_order() -> None:
    first = Orchestrator._validate_review_fragment(
        {
            "verdict": "approved",
            "reasons": ["El movimiento está implementado."],
            "acceptance_results": {"Movimiento funcional": True},
        },
        ["Movimiento funcional", "Jugador azul"],
    )
    second = Orchestrator._validate_review(
        {
            "verdict": "changes_requested",
            "reasons": ["El jugador aún no es azul."],
            "acceptance_results": {"Jugador azul": False},
        },
        ["Jugador azul"],
    )

    merged = Orchestrator._merge_review_fragments(
        [first, second],
        ["Movimiento funcional", "Jugador azul"],
    )

    assert merged.verdict == "changes_requested"
    assert merged.acceptance_results == {
        "Movimiento funcional": True,
        "Jugador azul": False,
    }
    assert merged.reasons == [
        "El movimiento está implementado.",
        "El jugador aún no es azul.",
    ]


def test_invalid_initial_review_is_recovered_with_focused_coverage() -> None:
    fragments, error = Orchestrator._recover_initial_review_fragment(
        {
            "verdict": "approved",
            "reasons": ["The implementation appears complete."],
            "acceptance_results": {"unexpected criterion": True},
        },
        ["criterion one", "criterion two"],
    )

    assert fragments == []
    assert error is not None
    assert "unexpected criteria" in error


def test_focused_review_remaps_its_only_result_to_the_requested_criterion() -> None:
    proposal = Orchestrator._validate_focused_review(
        {
            "verdict": "approved",
            "reasons": ["The blue player is visually distinct."],
            "acceptance_results": {"Player is blue": True},
        },
        "El personaje principal es azul",
    )

    assert proposal.verdict == "approved"
    assert proposal.acceptance_results == {
        "El personaje principal es azul": True,
    }
    assert proposal.reasons == ["The blue player is visually distinct."]


def test_focused_review_uses_explicit_verdict_when_result_map_is_unusable() -> None:
    proposal = Orchestrator._validate_focused_review(
        {
            "verdict": "approved",
            "reasons": [
                "The player uses #0000FF and enemies use #FF0000.",
            ],
            "acceptance_results": {},
        },
        "El personaje principal es azul",
    )

    assert proposal.verdict == "approved"
    assert proposal.acceptance_results == {
        "El personaje principal es azul": True,
    }


def test_recovered_candidate_must_belong_to_the_same_task() -> None:
    item = WorkItem(
        id="current",
        project_id="project",
        milestone_id="milestone",
        title="Repair game",
        description="Recover verified candidate",
        expected_outputs=["game"],
        acceptance_criteria=["Playable"],
    )
    artifact = Artifact(
        project_id="project",
        work_item_id="current",
        agent_run_id="worker",
        artifact_type="Code",
        title="Candidate",
        content={
            "artifact_type": "Code",
            "title": "Candidate",
            "summary": "Playable candidate",
            "quality": "verified",
            "acceptance_criteria_addressed": ["Playable"],
            "files": [
                {
                    "path": "game.js",
                    "content": "requestAnimationFrame(update);",
                    "purpose": "Game loop",
                }
            ],
            "isolation": {"branch": "discarded-branch"},
        },
    )

    proposal = Orchestrator._recovery_work_proposal(artifact, item)

    assert proposal.files[0].path == "game.js"
    assert not hasattr(proposal, "isolation")
    with pytest.raises(ValueError, match="approved direct dependency"):
        Orchestrator._recovery_work_proposal(
            artifact.model_copy(update={"work_item_id": "different"}),
            item,
        )


def test_recovered_candidate_can_seed_from_an_approved_direct_dependency() -> None:
    item = WorkItem(
        id="revision",
        project_id="project",
        milestone_id="milestone",
        title="Advanced revision",
        description="Add lives and victory",
        expected_outputs=["game"],
        dependency_ids=["completed-task"],
        acceptance_criteria=["Lives decrease"],
    )
    artifact = Artifact(
        id="approved-source",
        project_id="project",
        work_item_id="completed-task",
        agent_run_id="worker",
        artifact_type="Code",
        title="Playable baseline",
        content={
            "artifact_type": "Code",
            "title": "Playable baseline",
            "summary": "Previously approved game",
            "quality": "verified",
            "acceptance_criteria_addressed": ["Playable"],
            "files": [
                {
                    "path": "game.js",
                    "content": "const player = {x: 0, y: 0};",
                    "purpose": "Game baseline",
                }
            ],
        },
    )

    proposal = Orchestrator._recovery_work_proposal(
        artifact,
        item,
        approved_dependency_artifact_ids={"approved-source"},
    )

    assert proposal.files[0].path == "game.js"


def test_valid_tester_contract_is_normalized() -> None:
    proposal = Orchestrator._validate_test(
        {
            "passed": True,
            "checks": [
                {
                    "name": "syntax",
                    "passed": True,
                    "evidence": "Node terminó con código 0.",
                }
            ],
            "summary": "La evidencia fija fue verificada.",
        }
    )

    assert proposal.passed is True
    assert proposal.checks[0].name == "syntax"


def test_invalid_tester_explanation_falls_back_to_fixed_gate() -> None:
    proposal = Orchestrator._fallback_test_evaluation(
        report_passed=True,
        reason="checks.1.evidence cannot be empty",
    )

    assert proposal.passed is True
    assert proposal.checks[0].passed is False
    assert "cannot be empty" in proposal.checks[0].evidence


def test_model_assessment_cannot_override_fixed_technical_evidence() -> None:
    assert Orchestrator._technical_evidence_passed(
        file_verified=True,
        profiles_passed=True,
    )
    assert not Orchestrator._technical_evidence_passed(
        file_verified=False,
        profiles_passed=True,
    )


def test_failed_local_profile_overrides_positive_model_review() -> None:
    proposal = ReviewEvaluationProposal(
        verdict="approved",
        reasons=["El modelo cree que funciona."],
        acceptance_results={"El juego abre": True},
    )

    gated = Orchestrator._apply_technical_review_gate(
        proposal,
        ["El juego abre"],
        report_passed=False,
        validation_checks=[
            {
                "profile": "web_application",
                "passed": False,
                "stderr": "getContext requires canvas",
            }
        ],
    )

    assert gated.verdict == "changes_requested"
    assert gated.acceptance_results == {"El juego abre": False}
    assert "getContext requires canvas" in gated.reasons[0]


def test_semantic_review_payload_excludes_tester_opinions() -> None:
    payload = Orchestrator._review_payload(
        {
            "title": "Tres vecinas",
            "acceptance_criteria_addressed": ["Declaración no confiable"],
            "evidence": ["Afirmación del trabajador"],
            "isolation": {"backend": "git_worktree"},
        },
        [{"path": "docs/design.md", "content": "1. A\n2. B\n3. C"}],
        ["Incluye al menos tres tipos"],
        [],
    )

    assert payload == {
        "artifact": {"title": "Tres vecinas"},
        "workspace_file_contents": [
            {"path": "docs/design.md", "content": "1. A\n2. B\n3. C"}
        ],
        "acceptance_criteria": ["Incluye al menos tres tipos"],
        "dependency_artifacts": [],
    }
    assert "test_report" not in payload
    assert "acceptance_criteria_addressed" not in payload["artifact"]
    assert "isolation" not in payload["artifact"]


def test_foreign_criterion_declaration_is_detected_without_rejecting_content() -> None:
    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "Code",
            "title": "Entrega copiada",
            "summary": "No corresponde a la tarea actual.",
            "quality": "draft",
            "acceptance_criteria_addressed": ["Un criterio de la dependencia"],
            "files": [
                {
                    "path": "docs/design.md",
                    "content": "Contenido anterior",
                    "purpose": "Referencia",
                }
            ],
        }
    )
    item = WorkItem(
        project_id="project",
        milestone_id="milestone",
        title="Implementar lógica",
        description="Crear el juego",
        expected_outputs=["Código"],
        acceptance_criteria=["El personaje principal es azul"],
    )

    assert not Orchestrator._work_declaration_matches_item(proposal, item)


def test_partial_criterion_declaration_matches_the_current_task() -> None:
    proposal = WorkArtifactProposal.model_validate(
        {
            "artifact_type": "Code",
            "title": "Juego inicial",
            "summary": "Implementa el personaje y los enemigos.",
            "quality": "draft",
            "acceptance_criteria_addressed": ["El personaje principal es azul"],
            "files": [
                {
                    "path": "game.js",
                    "content": "const player = { color: 'blue' };",
                    "purpose": "Lógica inicial",
                }
            ],
        }
    )
    item = WorkItem(
        project_id="project",
        milestone_id="milestone",
        title="Implementar lógica",
        description="Crear el juego",
        expected_outputs=["Código"],
        acceptance_criteria=[
            "El personaje principal es azul",
            "El juego sigue reglas tipo Pac-Man",
        ],
    )

    assert Orchestrator._work_declaration_matches_item(proposal, item)
