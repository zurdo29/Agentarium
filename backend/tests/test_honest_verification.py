from __future__ import annotations

import pytest
from agentarium.domain.enums import RiskLevel, VerificationMode, WorkItemStatus
from agentarium.domain.models import Milestone, Project, WorkItem
from agentarium.execution import WorkArtifactProposal, WorkspaceFileProposal
from agentarium.isolation import WorktreeSession
from agentarium.llm import ModelRequest
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService

# Gate-MVP.2 (ADR 0041): direct regression for the textkit-slugify incident
# (Gate pre-MVP evidence run, PR #27) -- a work item on an imported project
# reached `completed` with a `NameError` (worker rewrote `slugify` and
# dropped `import re`) and silently deleted 2 preexisting tests
# (`SlugifyTests.test_basic_lowercase`/`test_strips_accents`) inside a
# renamed class, replacing them with 1 new one. The critical_reviewer (LLM)
# approved it claiming the code "processes correctly" -- never actually
# executed, since P3.4/ADR 0034 blocks SCRIPT_EXECUTION for imported
# projects. This file reconstructs the same base/candidate content byte for
# byte and proves the mechanical gates this PR adds actually fire: F821
# rejects the missing import without running anything, the report is
# honestly labeled static_only rather than silently passing as if
# executed, and the deleted tests show up as real, persisted evidence.

_BASE_SLUG_PY = (
    "import re\n"
    "import unicodedata\n\n\n"
    "def slugify(value: str) -> str:\n"
    "    normalized = unicodedata.normalize('NFKD', value)\n"
    "    ascii_only = normalized.encode('ascii', 'ignore').decode('ascii')\n"
    "    lowered = ascii_only.lower()\n"
    "    return re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')\n"
)

# Same body as base -- only `import re` is missing. slugify() keeps its
# name (PYTHON_UNDEFINED_NAMES, not _removed_top_level_definitions, is what
# catches this half of the incident).
_CANDIDATE_SLUG_PY = (
    "import unicodedata\n\n\n"
    "def slugify(value: str) -> str:\n"
    "    normalized = unicodedata.normalize('NFKD', value)\n"
    "    ascii_only = normalized.encode('ascii', 'ignore').decode('ascii')\n"
    "    lowered = ascii_only.lower()\n"
    "    return re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')\n"
)

_BASE_TEST_SLUG_PY = (
    "import unittest\n\n"
    "from textkit.slug import slugify\n\n\n"
    "class SlugifyTests(unittest.TestCase):\n"
    "    def test_basic_lowercase(self):\n"
    "        self.assertEqual(slugify('Hello World'), 'hello-world')\n\n"
    "    def test_strips_accents(self):\n"
    "        self.assertEqual(slugify('cafe'), 'cafe')\n"
)

# Real shape of the incident: class renamed, both original tests gone,
# replaced by 1 new one in the renamed class.
_CANDIDATE_TEST_SLUG_PY = (
    "import unittest\n\n"
    "from textkit.slug import slugify\n\n\n"
    "class SlugTests(unittest.TestCase):\n"
    "    def test_slug_roundtrip(self):\n"
    "        self.assertEqual(slugify('Hello World'), 'hello-world')\n"
)


def _project(service: ApplicationService, *, imported: bool) -> Project:
    return service.repository.create_project(
        Project(
            title="textkit-slugify" if imported else "Greenfield tool",
            goal="Normalizar texto a slugs ASCII validos",
            imported=imported,
        )
    )


def _milestone(service: ApplicationService, project_id: str) -> Milestone:
    milestone = Milestone(
        project_id=project_id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    return milestone


def _slug_work_item(project_id: str, milestone_id: str) -> WorkItem:
    return WorkItem(
        project_id=project_id,
        milestone_id=milestone_id,
        title="Corregir slugify",
        description="Ajustar textkit/slug.py y sus pruebas",
        expected_outputs=["textkit/slug.py", "tests/test_slug.py"],
        acceptance_criteria=["slugify convierte texto a un slug ASCII valido"],
        max_attempts=3,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )


async def _seed_base_files(service: ApplicationService, project_id: str) -> None:
    """Puts the correct, pre-incident textkit/slug.py + tests/test_slug.py
    onto `main` directly (prepare/stage/collect/integrate/discard), the
    same 4-call sequence test_git_worktree_isolation.py's own
    read_base_file tests use -- so the candidate submitted afterwards has
    real prior content to be compared against, without needing a full
    import/LLM round trip just to seed history."""
    isolation = service.orchestrator.isolation
    session: WorktreeSession = await isolation.prepare(project_id, "seed", 0)
    service.orchestrator.workspace.stage(
        project_id,
        session.path,
        [
            WorkspaceFileProposal(
                path="textkit/slug.py",
                content=_BASE_SLUG_PY,
                purpose="Implementacion original de slugify",
            ),
            WorkspaceFileProposal(
                path="tests/test_slug.py",
                content=_BASE_TEST_SLUG_PY,
                purpose="Pruebas originales de slugify",
            ),
        ],
    )
    changes = await isolation.collect(session)
    await isolation.integrate(changes)
    await isolation.discard(session)


def _command_evidence_entries(
    service: ApplicationService, project_id: str, check: str
) -> list[dict[str, object]]:
    (report,) = service.repository.list_test_reports(project_id)
    return [entry for entry in report.command_evidence if entry.get("check") == check]


@pytest.mark.asyncio
async def test_incident_candidate_fails_undefined_names_and_is_marked_static_only(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(service, imported=True)
    milestone = _milestone(service, project.id)
    item = _slug_work_item(project.id, milestone.id)
    service.repository.add_work_item(item)
    await _seed_base_files(service, project.id)

    review_payloads: list[dict[str, object]] = []
    original_generate = MockProvider.generate

    async def capturing_generate(self, request: ModelRequest, agent):  # type: ignore[no-untyped-def]
        if request.operation == "review":
            review_payloads.append(request.payload)
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", capturing_generate)

    proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Correccion de slugify",
        summary="Reescribe slugify y actualiza sus pruebas.",
        quality="verified",
        files=[
            WorkspaceFileProposal(
                path="textkit/slug.py",
                content=_CANDIDATE_SLUG_PY,
                purpose="slugify reescrito",
            ),
            WorkspaceFileProposal(
                path="tests/test_slug.py",
                content=_CANDIDATE_TEST_SLUG_PY,
                purpose="pruebas actualizadas",
            ),
        ],
    )

    await service.orchestrator.evaluate_operator_candidate(item.id, proposal)

    (report,) = service.repository.list_test_reports(project.id)

    undefined_names = _command_evidence_entries(
        service, project.id, "validation_profile"
    )
    slug_check = next(
        entry
        for entry in undefined_names
        if entry.get("profile") == "python_undefined_names"
        and entry.get("targets") == ["textkit/slug.py"]
    )
    assert slug_check["passed"] is False
    assert "re" in (slug_check["stdout"] or "") + (slug_check["stderr"] or "")

    # The fixed-evidence gate: PYTHON_UNDEFINED_NAMES failing means
    # report_passed=False regardless of what the LLM tester/reviewer said.
    assert report.passed is False

    # Imported project: SCRIPT_EXECUTION never even attempted.
    assert report.verification_mode is VerificationMode.STATIC_ONLY

    removed = _command_evidence_entries(service, project.id, "removed_top_level_names")
    removed_for_tests = next(
        entry for entry in removed if entry["path"] == "tests/test_slug.py"
    )
    assert set(removed_for_tests["removed"]) == {
        "SlugifyTests",
        "SlugifyTests.test_basic_lowercase",
        "SlugifyTests.test_strips_accents",
    }
    removed_for_slug = next(
        entry for entry in removed if entry["path"] == "textkit/slug.py"
    )
    # slugify() kept its name -- this mechanism must not flag it; F821 is
    # what catches that half of the incident, not this one.
    assert removed_for_slug["removed"] == []

    reloaded = service.repository.get_work_item(item.id)
    assert reloaded.status is not WorkItemStatus.COMPLETED

    # Whatever review round(s) actually happened, each one carried the
    # real removed-names map -- never silently dropped for a focused
    # follow-up review.
    assert review_payloads
    for payload in review_payloads:
        assert payload["removed_top_level_names"]["tests/test_slug.py"] == [
            "SlugifyTests",
            "SlugifyTests.test_basic_lowercase",
            "SlugifyTests.test_strips_accents",
        ]


@pytest.mark.asyncio
async def test_correct_candidate_triggers_neither_undefined_names_nor_removed_names(
    service: ApplicationService,
) -> None:
    """Negative control: same imported project, same base history, a
    candidate that keeps the import and doesn't drop any test."""
    project = _project(service, imported=True)
    milestone = _milestone(service, project.id)
    item = _slug_work_item(project.id, milestone.id)
    service.repository.add_work_item(item)
    await _seed_base_files(service, project.id)

    proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Ajuste menor de slugify",
        summary="Mantiene el import y las pruebas existentes.",
        quality="verified",
        files=[
            WorkspaceFileProposal(
                path="textkit/slug.py",
                content=_BASE_SLUG_PY,
                purpose="slugify sin cambios de fondo",
            ),
            WorkspaceFileProposal(
                path="tests/test_slug.py",
                content=_BASE_TEST_SLUG_PY,
                purpose="pruebas sin cambios",
            ),
        ],
    )

    await service.orchestrator.evaluate_operator_candidate(item.id, proposal)

    undefined_names = _command_evidence_entries(
        service, project.id, "validation_profile"
    )
    assert all(
        entry["passed"]
        for entry in undefined_names
        if entry.get("profile") == "python_undefined_names"
    )
    removed = _command_evidence_entries(service, project.id, "removed_top_level_names")
    assert all(entry["removed"] == [] for entry in removed)


@pytest.mark.asyncio
async def test_non_imported_project_that_executes_and_fails_is_marked_executed(
    service: ApplicationService,
) -> None:
    """Gate-MVP.2 correction 1: verification_mode must never be derived
    from `passed`. A non-imported project's SCRIPT_EXECUTION really
    launches here and really fails (RuntimeError) -- executed, not
    static_only, is the only honest label for that."""
    project = _project(service, imported=False)
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Entregar tool.py",
        description="Entregar una herramienta ejecutable",
        expected_outputs=["tool.py"],
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
        max_attempts=3,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)

    proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Herramienta que falla en tiempo de ejecucion",
        summary="Se ejecuta de verdad y falla.",
        quality="verified",
        files=[
            WorkspaceFileProposal(
                path="tool.py",
                content="raise RuntimeError('deliberate failure')\n",
                purpose="Script que falla al correr",
            )
        ],
    )

    await service.orchestrator.evaluate_operator_candidate(item.id, proposal)

    (report,) = service.repository.list_test_reports(project.id)
    assert report.passed is False
    assert report.verification_mode is VerificationMode.EXECUTED
