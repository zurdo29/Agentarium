"""P1.1: everything the benchmark does must be reproducible without inference.

The whole suite here runs on the `mock` provider, so the matrix machinery,
the taxonomy and the report are exercised deterministically. Real models are
P1.2's problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agentarium.benchmarks import (
    BenchmarkCase,
    BenchmarkLedger,
    BenchmarkRunner,
    FailureCategory,
    InvalidBenchmarkCase,
    ModelTarget,
    build_payload,
    cases_root,
    load_cases,
    plan_matrix,
    render_markdown,
)
from agentarium.benchmarks.contracts import CASE_SCHEMA_VERSION, BenchmarkRunRecord
from agentarium.domain.enums import ProjectStatus, WorkItemStatus
from agentarium.domain.models import Project, WorkItem
from agentarium.services import ApplicationService
from pydantic import ValidationError

# --- the versioned cases ----------------------------------------------------


def test_the_three_versioned_cases_load_and_declare_their_schema() -> None:
    cases = load_cases()
    assert {case.id for case in cases} == {
        "csv_expenses_cli",
        "architecture_document",
        "library_api_sqlite",
    }
    for case in cases:
        assert case.schema_version == CASE_SCHEMA_VERSION
        assert case.validators, case.id
        assert case.goal.strip()


def test_every_case_file_is_named_after_its_id() -> None:
    for path in sorted(cases_root().glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["id"] == path.stem


def test_a_case_whose_id_disagrees_with_its_filename_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "uno.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "otro",
                "title": "T",
                "goal": "G",
                "expected_artifacts": ["A"],
                "validators": [
                    {"kind": "file_exists", "description": "D", "path_glob": "*.py"}
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidBenchmarkCase, match="no coincide"):
        load_cases(tmp_path)


def test_a_validator_cannot_reach_outside_the_delivery() -> None:
    for glob in ("../secreto.py", "/etc/passwd"):
        with pytest.raises(ValidationError, match="path_glob"):
            BenchmarkCase.model_validate(
                {
                    "schema_version": 1,
                    "id": "x",
                    "title": "T",
                    "goal": "G",
                    "expected_artifacts": ["A"],
                    "validators": [
                        {"kind": "file_exists", "description": "D", "path_glob": glob}
                    ],
                }
            )


def test_file_matches_requires_a_pattern_and_the_others_reject_one() -> None:
    with pytest.raises(ValidationError, match="requires a pattern"):
        BenchmarkCase.model_validate(_case_payload({"kind": "file_matches"}))
    with pytest.raises(ValidationError, match="does not take a pattern"):
        BenchmarkCase.model_validate(
            _case_payload({"kind": "file_exists", "pattern": "algo"})
        )


def _case_payload(validator: dict[str, object]) -> dict[str, object]:
    base = {"description": "D", "path_glob": "*.py"}
    base.update(validator)
    return {
        "schema_version": 1,
        "id": "x",
        "title": "T",
        "goal": "G",
        "expected_artifacts": ["A"],
        "validators": [base],
    }


# --- validators read files, not summaries -----------------------------------


def _case(**overrides: object) -> BenchmarkCase:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": "demo",
        "title": "Demo",
        "goal": "Un objetivo",
        "expected_artifacts": ["Algo"],
        "validators": [
            {"kind": "file_exists", "description": "Hay un script", "path_glob": "**/*.py"},
            {
                "kind": "file_matches",
                "description": "El script lee un CSV",
                "path_glob": "**/*.py",
                "pattern": "csv",
            },
            {
                "kind": "file_absent",
                "description": "No quedan temporales",
                "path_glob": "**/*.tmp",
            },
        ],
    }
    payload.update(overrides)
    return BenchmarkCase.model_validate(payload)


def test_validators_pass_only_when_the_files_really_say_so(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import csv\n", encoding="utf-8")

    outcomes = BenchmarkRunner.validate_delivery(_case(), tmp_path)

    assert [outcome.passed for outcome in outcomes] == [True, True, True]


def test_a_missing_delivery_fails_every_validator(tmp_path: Path) -> None:
    outcomes = BenchmarkRunner.validate_delivery(_case(), tmp_path / "no-existe")

    assert outcomes
    assert not any(outcome.passed for outcome in outcomes)
    assert all("no se materializó" in outcome.detail for outcome in outcomes)


def test_a_file_that_exists_but_lacks_the_pattern_fails(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hola')\n", encoding="utf-8")
    (tmp_path / "resto.tmp").write_text("basura", encoding="utf-8")

    outcomes = BenchmarkRunner.validate_delivery(_case(), tmp_path)

    assert [outcome.passed for outcome in outcomes] == [True, False, False]


# --- taxonomy ---------------------------------------------------------------


def _project(status: ProjectStatus) -> Project:
    return Project(title="T", goal="G", status=status)


def _item(status: WorkItemStatus, error: str | None = None) -> WorkItem:
    return WorkItem(
        project_id="p",
        milestone_id="m",
        title="Tarea",
        description="D",
        expected_outputs=["x"],
        acceptance_criteria=["c"],
        status=status,
        last_error=error,
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            "Retry candidate files are byte-for-byte identical to the previous",
            FailureCategory.DUPLICATE_CANDIDATE,
        ),
        (
            "Workspace file paths collide with files already owned by an unrelated task",
            FailureCategory.PATH_CONFLICT,
        ),
        ("ModuleNotFoundError: No module named 'flask'", FailureCategory.UNSUPPORTED_CAPABILITY),
        ("Workspace file could not be written: api.py", FailureCategory.INFRASTRUCTURE),
        ("Invalid workspace artifact: 1 validation error", FailureCategory.TECHNICAL_VALIDATION),
    ],
)
def test_the_taxonomy_maps_each_known_failure(error: str, expected: FailureCategory) -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.FAILED),
        [_item(WorkItemStatus.FAILED, error)],
        [],
        validation_passed=False,
    )

    assert result.category is expected


def test_a_completed_project_whose_files_fail_validation_is_not_completed() -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.COMPLETED),
        [_item(WorkItemStatus.COMPLETED)],
        [],
        validation_passed=False,
    )

    # The false completion the benchmark exists to count.
    assert result.category is FailureCategory.TECHNICAL_VALIDATION


def test_a_completed_and_validated_project_is_completed() -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.COMPLETED),
        [_item(WorkItemStatus.COMPLETED)],
        [],
        validation_passed=True,
    )

    assert result.category is FailureCategory.COMPLETED


def test_a_provider_failure_outranks_a_later_symptom() -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.FAILED),
        [_item(WorkItemStatus.FAILED, "byte-for-byte identical")],
        [{"action": "agent_run_failed", "message": "ConnectTimeout"}],
        validation_passed=False,
    )

    assert result.category is FailureCategory.PROVIDER_FAILURE
    assert "ConnectTimeout" in result.evidence


# --- matrix planning and resumability ---------------------------------------


def test_the_matrix_is_the_full_product(tmp_path: Path) -> None:
    cases = [_case(), _case(id="otro")]
    targets = [ModelTarget.parse("mock"), ModelTarget.parse("ollama:qwen3:4b")]

    planned = plan_matrix(cases, targets, 3)

    assert len(planned) == 2 * 2 * 3
    assert len({run.key for run in planned}) == len(planned)


def test_a_target_carries_provider_and_model() -> None:
    target = ModelTarget.parse("ollama:qwen2.5-coder:7b")
    assert target.provider == "ollama"
    # Everything after the first colon is the model, tags included.
    assert target.model == "qwen2.5-coder:7b"
    assert ModelTarget.parse("mock").model == ""


def _record(case_id: str, repetition: int, **overrides: object) -> BenchmarkRunRecord:
    payload: dict[str, object] = {
        "case_id": case_id,
        "case_schema_version": 1,
        "provider": "mock",
        "model": "",
        "repetition": repetition,
        "project_id": "p",
        "project_status": "completed",
        "progress_percent": 100.0,
        "duration_seconds": 1.0,
        "attempts": 3,
        "splits": 0,
        "technical_result": True,
        "semantic_result": True,
        "validation_passed": True,
        "validators": [],
        "category": FailureCategory.COMPLETED,
        "evidence": "ok",
    }
    payload.update(overrides)
    return BenchmarkRunRecord.model_validate(payload)


def test_pending_skips_combinations_already_in_the_ledger(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1))
    runner = BenchmarkRunner(service, ledger)
    planned = plan_matrix([_case()], [ModelTarget.parse("mock")], 3)

    pending = runner.pending(planned)

    assert [run.repetition for run in pending] == [2, 3]


def test_the_ledger_survives_being_reopened(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    BenchmarkLedger(path).append(_record("demo", 1))
    BenchmarkLedger(path).append(_record("demo", 2))

    assert len(BenchmarkLedger(path).records()) == 2
    assert BenchmarkLedger(path).completed_keys() == {
        ("demo", "mock", "", 1),
        ("demo", "mock", "", 2),
    }


def test_a_corrupt_ledger_line_is_reported_not_ignored(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"nope": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="línea 1"):
        BenchmarkLedger(path).records()


def test_a_rerun_of_the_same_key_supersedes_the_previous_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = BenchmarkLedger(path)
    ledger.append(_record("demo", 1, category=FailureCategory.DUPLICATE_CANDIDATE))
    ledger.append(_record("demo", 1))

    latest = ledger.latest_by_key()

    assert len(latest) == 1
    assert latest[("demo", "mock", "", 1)].category is FailureCategory.COMPLETED


# --- report -----------------------------------------------------------------


def test_the_report_is_built_from_the_ledger_alone() -> None:
    records = [
        _record("demo", 1),
        _record("demo", 2, category=FailureCategory.DUPLICATE_CANDIDATE,
                project_status="failed", validation_passed=False,
                technical_result=False, semantic_result=False),
        _record("otro", 1, project_status="completed", validation_passed=False,
                category=FailureCategory.TECHNICAL_VALIDATION),
    ]

    payload = build_payload(records)

    assert payload["totals"]["runs"] == 3
    assert payload["totals"]["completed"] == 1
    assert payload["totals"]["false_completed"] == 1
    assert payload["categories"]["duplicate_candidate"] == 1
    assert payload["by_target"]["mock"]["runs"] == 3

    markdown = render_markdown(payload)
    assert "# Informe de benchmark" in markdown
    assert "Falsos `completed`" in markdown
    assert "duplicate_candidate" in markdown


def test_an_empty_ledger_still_renders_a_report() -> None:
    markdown = render_markdown(build_payload([]))
    assert "No hay corridas en el ledger todavía." in markdown


# --- the 1x1x1 smoke --------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_one_case_one_model_one_repetition(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    """P1.1's exit criterion: a full reproducible report with no real inference."""
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = BenchmarkRunner(service, ledger)
    case = _case(
        validators=[
            {
                "kind": "file_exists",
                "description": "La entrega materializó algún archivo",
                "path_glob": "**/*",
            }
        ]
    )
    planned = plan_matrix([case], [ModelTarget.parse("mock")], 1)

    records = await runner.execute(planned)

    assert len(records) == 1
    record = records[0]
    assert record.project_id is not None
    assert record.duration_seconds >= 0
    assert record.prompt_versions["planning"]
    assert record.category is FailureCategory.COMPLETED, record.evidence
    assert record.validation_passed
    assert not record.false_completed

    # Reproducible: the report comes from the ledger, and a second execute()
    # adds nothing because the combination is already done.
    payload = build_payload(list(ledger.latest_by_key().values()))
    assert payload["totals"] == {
        "runs": 1,
        "completed": 1,
        "completion_rate": 100.0,
        "false_completed": 0,
    }
    assert "# Informe de benchmark" in render_markdown(payload)

    assert await runner.execute(planned) == []
    assert len(ledger.records()) == 1
