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
    RuntimeIdentity,
    build_payload,
    cases_root,
    load_cases,
    plan_matrix,
    prompt_versions,
    render_markdown,
)
from agentarium.benchmarks.contracts import CASE_SCHEMA_VERSION, BenchmarkRunRecord
from agentarium.domain.enums import ProjectStatus, ReviewVerdict, WorkItemStatus
from agentarium.domain.models import Project, Review, WorkItem

# Aliased: pytest tries to collect anything named Test* as a test class.
from agentarium.domain.models import TestReport as ReportModel
from agentarium.services import ApplicationService
from pydantic import ValidationError
from typer.testing import CliRunner

# --- the versioned cases ----------------------------------------------------


def test_the_three_versioned_cases_load_and_declare_their_schema() -> None:
    cases = load_cases()
    assert {case.id for case in cases} == {
        "csv_expenses_cli",
        "architecture_document",
        "library_api_sqlite",
    }
    for case in cases:
        # Cases version independently: only the one whose contract changed
        # moves. A single shared version would force churn on the others.
        assert 1 <= case.schema_version <= CASE_SCHEMA_VERSION, case.id
        assert case.validators, case.id
        assert case.goal.strip()

    csv_case = next(case for case in cases if case.id == "csv_expenses_cli")
    assert csv_case.schema_version == 2, "el contrato funcional exige la versión 2"
    assert csv_case.functional is not None


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


def test_a_transient_provider_failure_does_not_hide_the_terminal_cause() -> None:
    # Ollama died once at attempt 1, the run recovered, and the task finally
    # died on a duplicate candidate at attempt 3. The run is a duplicate.
    from agentarium.benchmarks import classify

    item = _item(WorkItemStatus.FAILED, "Retry candidate files are byte-for-byte identical")
    item = item.model_copy(update={"attempt_count": 3})

    result = classify(
        _project(ProjectStatus.FAILED),
        [item],
        [
            {
                "action": "agent_run_failed",
                "work_item_id": item.id,
                "attempt": 1,
                "message": "ConnectTimeout",
            }
        ],
        validation_passed=False,
    )

    assert result.category is FailureCategory.DUPLICATE_CANDIDATE


def test_a_provider_failure_on_the_final_attempt_is_the_terminal_cause() -> None:
    from agentarium.benchmarks import classify

    item = _item(WorkItemStatus.FAILED, "ConnectTimeout")
    item = item.model_copy(update={"attempt_count": 3})

    result = classify(
        _project(ProjectStatus.FAILED),
        [item],
        [
            {
                "action": "agent_run_failed",
                "work_item_id": item.id,
                "attempt": 3,
                "message": "ConnectTimeout",
            }
        ],
        validation_passed=False,
    )

    assert result.category is FailureCategory.PROVIDER_FAILURE


def test_a_provider_failure_during_planning_is_not_a_planning_contract() -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.FAILED),
        [],
        [
            {"action": "agent_run_failed", "work_item_id": None, "message": "timeout"},
            {"action": "planning_failed", "error": "El proveedor no respondió"},
        ],
        validation_passed=False,
    )

    assert result.category is FailureCategory.PROVIDER_FAILURE


def test_a_planner_that_answered_badly_is_a_planning_contract() -> None:
    from agentarium.benchmarks import classify

    result = classify(
        _project(ProjectStatus.FAILED),
        [],
        [{"action": "planning_failed", "error": "Invalid plan: falta el hito"}],
        validation_passed=False,
    )

    assert result.category is FailureCategory.PLANNING_CONTRACT


def test_a_final_semantic_rejection_survives_an_earlier_provider_hiccup() -> None:
    from agentarium.benchmarks import classify

    item = _item(WorkItemStatus.FAILED, "El revisor rechazó la entrega")
    item = item.model_copy(update={"attempt_count": 3})
    review = Review(
        project_id="p",
        work_item_id=item.id,
        artifact_id="a",
        reviewer_run_id="r",
        verdict=ReviewVerdict.CHANGES_REQUESTED,
        reasons=["No cubre el criterio de préstamo"],
        acceptance_results={},
    )

    result = classify(
        _project(ProjectStatus.FAILED),
        [item],
        [
            {
                "action": "agent_run_failed",
                "work_item_id": item.id,
                "attempt": 1,
                "message": "ConnectTimeout",
            }
        ],
        validation_passed=False,
        reviews=[review],
    )

    assert result.category is FailureCategory.SEMANTIC_REJECTION
    assert "préstamo" in result.evidence


def test_the_terminal_task_is_the_one_that_failed_last() -> None:
    from datetime import timedelta

    from agentarium.benchmarks import classify

    early = _item(WorkItemStatus.FAILED, "Workspace file could not be written: x.py")
    late = _item(WorkItemStatus.FAILED, "Retry candidate files are byte-for-byte identical")
    late = late.model_copy(update={"updated_at": early.updated_at + timedelta(minutes=5)})

    result = classify(
        _project(ProjectStatus.FAILED),
        [early, late],
        [],
        validation_passed=False,
    )

    assert result.category is FailureCategory.DUPLICATE_CANDIDATE


# --- the two gates ----------------------------------------------------------


def _review(work_item_id: str, verdict: ReviewVerdict) -> Review:
    return Review(
        project_id="p",
        work_item_id=work_item_id,
        artifact_id="a",
        reviewer_run_id="r",
        verdict=verdict,
        reasons=["motivo"],
        acceptance_results={},
    )


def _report(work_item_id: str, passed: bool) -> ReportModel:
    return ReportModel(
        project_id="p",
        work_item_id=work_item_id,
        artifact_id="a",
        tester_run_id="t",
        passed=passed,
        checks=[],
        summary="resumen",
    )


def test_passing_technically_and_failing_semantically_is_reported_as_such() -> None:
    from agentarium.benchmarks import gate_results

    item = _item(WorkItemStatus.FAILED)
    gates = gate_results(
        [item],
        [_review(item.id, ReviewVerdict.CHANGES_REQUESTED)],
        [_report(item.id, True)],
    )

    # The exact case the old derivation got wrong.
    assert gates.technical is True
    assert gates.semantic is False


def test_only_the_last_verdict_per_task_counts() -> None:
    from agentarium.benchmarks import gate_results

    item = _item(WorkItemStatus.COMPLETED)
    gates = gate_results(
        [item],
        [
            _review(item.id, ReviewVerdict.CHANGES_REQUESTED),
            _review(item.id, ReviewVerdict.APPROVED),
        ],
        [_report(item.id, False), _report(item.id, True)],
    )

    assert gates.technical is True
    assert gates.semantic is True


def test_a_red_technical_report_is_not_filed_as_a_semantic_rejection() -> None:
    # `_apply_technical_review_gate` forces the review to CHANGES_REQUESTED
    # whenever the technical gate failed, so a red report always arrives with a
    # rejected review. The technical cause has to win.
    from agentarium.benchmarks import classify

    item = _item(WorkItemStatus.FAILED, "La compuerta técnica fija rechazó la entrega")
    review = _review(item.id, ReviewVerdict.CHANGES_REQUESTED)
    review = review.model_copy(
        update={"reasons": ["La compuerta técnica fija rechazó la entrega"]}
    )

    result = classify(
        _project(ProjectStatus.FAILED),
        [item],
        [],
        validation_passed=False,
        reviews=[review],
        test_reports=[_report(item.id, False)],
    )

    assert result.category is FailureCategory.TECHNICAL_VALIDATION


def test_a_rejection_the_technical_gate_did_not_cause_is_semantic() -> None:
    from agentarium.benchmarks import classify

    item = _item(WorkItemStatus.FAILED, "El revisor rechazó la entrega")
    review = _review(item.id, ReviewVerdict.CHANGES_REQUESTED)
    review = review.model_copy(
        update={"reasons": ["No cubre el criterio de préstamo"]}
    )

    result = classify(
        _project(ProjectStatus.FAILED),
        [item],
        [],
        validation_passed=False,
        reviews=[review],
        test_reports=[_report(item.id, True)],
    )

    assert result.category is FailureCategory.SEMANTIC_REJECTION
    assert "préstamo" in result.evidence


def test_a_parent_cancelled_by_a_split_does_not_count_against_the_gates() -> None:
    from agentarium.benchmarks import gate_results

    parent = _item(WorkItemStatus.CANCELLED)
    child = _item(WorkItemStatus.COMPLETED)
    gates = gate_results(
        [parent, child],
        [
            _review(parent.id, ReviewVerdict.CHANGES_REQUESTED),
            _review(child.id, ReviewVerdict.APPROVED),
        ],
        [_report(parent.id, False), _report(child.id, True)],
    )

    assert gates.technical is True
    assert gates.semantic is True
    assert gates.technical_reports == 1
    assert gates.semantic_reviews == 1


def test_a_gate_that_never_ran_is_not_vacuously_passed() -> None:
    from agentarium.benchmarks import gate_results

    gates = gate_results([_item(WorkItemStatus.FAILED)], [], [])

    assert gates.technical is False
    assert gates.semantic is False


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


def _identity() -> RuntimeIdentity:
    """A fixed identity, so these tests are about the ledger, not the machine."""
    return RuntimeIdentity(
        agentarium_commit="a" * 40,
        agentarium_dirty=False,
        python_version="3.14.0",
        platform="Windows-AMD64",
        concurrency=1,
    )


def _runner(service: ApplicationService, ledger: BenchmarkLedger) -> BenchmarkRunner:
    return BenchmarkRunner(service, ledger, identity=_identity())


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
        # Current by default: drift is something a test opts into.
        "prompt_versions": prompt_versions(),
        "runtime_identity": _identity().model_dump(),
    }
    payload.update(overrides)
    return BenchmarkRunRecord.model_validate(payload)


def test_pending_skips_combinations_already_in_the_ledger(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1))
    runner = _runner(service, ledger)
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


def test_the_ledger_rejects_a_record_from_another_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    payload = _record("demo", 1).model_dump(mode="json")
    payload["schema_version"] = 2
    path.write_text(__import__("json").dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="línea 1"):
        BenchmarkLedger(path).records()


def test_false_completed_travels_inside_the_serialized_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = BenchmarkLedger(path)
    ledger.append(_record("demo", 1, project_status="completed", validation_passed=False))

    raw = __import__("json").loads(path.read_text(encoding="utf-8").strip())
    assert raw["false_completed"] is True
    # And the record still round-trips through the ledger reader.
    assert ledger.records()[0].false_completed is True


def test_a_case_version_change_refuses_to_reuse_the_suite(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    from agentarium.benchmarks import SuiteDrift

    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1, case_schema_version=99))
    runner = _runner(service, ledger)

    with pytest.raises(SuiteDrift, match="--suite"):
        runner.pending(plan_matrix([_case()], [ModelTarget.parse("mock")], 1))


def test_a_prompt_version_change_refuses_to_reuse_the_suite(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    from agentarium.benchmarks import SuiteDrift

    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1, prompt_versions={"planning": "planning-v1"}))
    runner = _runner(service, ledger)

    with pytest.raises(SuiteDrift, match="prompts"):
        runner.pending(plan_matrix([_case()], [ModelTarget.parse("mock")], 1))


def test_an_unchanged_suite_is_comparable(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    from agentarium.benchmarks import prompt_versions

    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1, prompt_versions=prompt_versions()))
    runner = _runner(service, ledger)

    assert runner.pending(plan_matrix([_case()], [ModelTarget.parse("mock")], 1)) == []


def test_rerun_replans_a_combination_already_recorded(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    from agentarium.benchmarks import prompt_versions

    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record("demo", 1, prompt_versions=prompt_versions()))
    runner = _runner(service, ledger)
    planned = plan_matrix([_case()], [ModelTarget.parse("mock")], 1)

    assert runner.pending(planned) == []
    assert runner.pending(planned, rerun=True) == planned


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
    runner = _runner(service, ledger)
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


# --- the actual CLI commands ------------------------------------------------


def _cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at a throwaway project root."""
    from agentarium.config import settings as settings_module

    database = (tmp_path / "db.sqlite").as_posix()
    monkeypatch.setenv("AGENTARIUM_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("AGENTARIUM_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv(
        "AGENTARIUM_PROVIDER_STATE_PATH", str(tmp_path / "provider-selection.json")
    )
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        "agentarium.cli.project_root", lambda: tmp_path, raising=True
    )


def test_the_cli_rejects_an_invalid_suite_name_instead_of_sanitizing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        app, ["benchmark", "run", "--suite", "a/b", "--dry-run"]
    )

    assert result.exit_code != 0
    assert "inválido" in result.output


def test_the_cli_dry_run_lists_what_is_missing_without_executing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as json_module

    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "run",
            "--suite",
            "cli-smoke",
            "--case",
            "csv_expenses_cli",
            "--model",
            "mock",
            "--dry-run",
            "--allow-dirty",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json_module.loads(result.output[result.output.index("{"):])
    assert payload["pending"] == 1
    # The frozen identity is reported before anything runs.
    assert payload["identity"]["agentarium_commit"]
    assert "mock" in payload["model_digests"]
    assert not (tmp_path / "runtime" / "benchmarks" / "cli-smoke" / "ledger.jsonl").exists()


def test_the_cli_runs_reports_and_then_skips_what_is_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    runner = CliRunner()
    command = [
        "benchmark",
        "run",
        "--suite",
        "cli-smoke",
        "--case",
        "architecture_document",
        "--model",
        "mock",
        "--allow-dirty",
    ]

    first = runner.invoke(app, command)
    assert first.exit_code == 0, first.output
    ledger = tmp_path / "runtime" / "benchmarks" / "cli-smoke" / "ledger.jsonl"
    assert ledger.is_file()
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1

    # Resumable: the second invocation has nothing left to do.
    second = runner.invoke(app, command)
    assert second.exit_code == 0, second.output
    assert '"pending": 0' in second.output
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1

    # --rerun measures it again on purpose.
    third = runner.invoke(app, [*command, "--rerun"])
    assert third.exit_code == 0, third.output
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2

    report = runner.invoke(app, ["benchmark", "report", "--suite", "cli-smoke"])
    assert report.exit_code == 0, report.output
    assert "# Informe de benchmark" in report.output
    destination = tmp_path / "runtime" / "benchmarks" / "cli-smoke"
    assert (destination / "report.json").is_file()
    assert (destination / "report.md").is_file()
    # Superseded, not duplicated: one row per key.
    assert "Corridas registradas: **1**" in report.output


def test_the_architecture_case_rejects_a_document_that_only_echoes_the_goal(
    tmp_path: Path,
) -> None:
    """A validator satisfied by restating the request measures nothing.

    The mock provider echoes the goal, and the goal itself names "componentes",
    "alternativa" and "glosario" — so word-presence patterns passed on an
    artifact with no architecture in it at all.
    """
    case = next(one for one in load_cases() if one.id == "architecture_document")
    goal = next(
        line
        for line in [
            "Crear un documento de arquitectura en Markdown para un sistema de "
            "reservas: describe los componentes principales, las decisiones de "
            "diseño con al menos una alternativa considerada para cada una, y "
            "un glosario de los términos del dominio."
        ]
    )
    (tmp_path / "eco.md").write_text(
        f"# Especificar el alcance\n\n## Objetivo\n{goal}\n", encoding="utf-8"
    )

    outcomes = BenchmarkRunner.validate_delivery(case, tmp_path)

    assert not all(outcome.passed for outcome in outcomes)
    failed = {outcome.description for outcome in outcomes if not outcome.passed}
    assert "Hay una sección dedicada a los componentes" in failed
    assert "Hay un glosario con términos definidos" in failed


def test_the_architecture_case_accepts_a_document_with_real_structure(
    tmp_path: Path,
) -> None:
    case = next(one for one in load_cases() if one.id == "architecture_document")
    (tmp_path / "arquitectura.md").write_text(
        "# Arquitectura\n"
        "\n## Componentes principales\n"
        "- API de reservas\n"
        "\n## Decisiones de diseño\n"
        "- Persistencia en SQLite. Alternativa considerada: PostgreSQL.\n"
        "\n## Glosario\n"
        "- **Reserva**: intención de ocupar una mesa en una franja horaria.\n",
        encoding="utf-8",
    )

    outcomes = BenchmarkRunner.validate_delivery(case, tmp_path)

    assert all(outcome.passed for outcome in outcomes), [
        outcome.description for outcome in outcomes if not outcome.passed
    ]


# --- a failing validator must not take the matrix down ----------------------


@pytest.mark.asyncio
async def test_an_infrastructure_failure_is_recorded_and_the_matrix_continues(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """27 runs cannot die because one temp directory could not be copied."""
    from agentarium.benchmarks import functional as functional_module

    case = load_cases(only=["csv_expenses_cli"])[0]
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = _runner(service, ledger)
    planned = plan_matrix([case], [ModelTarget.parse("mock")], 2)

    # Injected at the staging step itself, so the failure does not depend on
    # what the provider happened to deliver.
    def broken_prepare(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(functional_module, "_prepare_run", broken_prepare)

    records = await runner.execute(planned)

    # Both combinations ran: the first failure did not abort the second.
    assert len(records) == 2
    assert [record.repetition for record in records] == [1, 2]
    assert all(record.category is FailureCategory.INFRASTRUCTURE for record in records)
    assert all("No space left" in record.evidence for record in records)

    # And both are on disk, so a resumed matrix does not repeat them.
    assert len(ledger.records()) == 2
    assert ledger.completed_keys() == {
        ("csv_expenses_cli", "mock", "", 1),
        ("csv_expenses_cli", "mock", "", 2),
    }
    assert runner.pending(planned) == []


@pytest.mark.asyncio
async def test_an_unexpected_crash_in_one_run_does_not_abort_the_rest(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = _runner(service, ledger)
    planned = plan_matrix([_case()], [ModelTarget.parse("mock")], 2)

    async def explode(self, run):  # type: ignore[no-untyped-def]
        raise RuntimeError("algo inesperado")

    monkeypatch.setattr(BenchmarkRunner, "execute_one", explode)

    records = await runner.execute(planned)

    assert len(records) == 2
    assert all(record.category is FailureCategory.INFRASTRUCTURE for record in records)
    assert all("algo inesperado" in str(record.error) for record in records)


def test_the_cli_refuses_to_measure_a_dirty_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A dirty tree means the recorded commit does not identify what ran, so the
    # measurement would be unattributable. Forced here instead of depending on
    # the state of this checkout.
    from agentarium.benchmarks import identity as identity_module
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    monkeypatch.setattr(identity_module, "git_commit", lambda *_: ("c" * 40, True))

    result = CliRunner().invoke(
        app,
        ["benchmark", "run", "--suite", "sucia", "--model", "mock", "--dry-run"],
    )

    assert result.exit_code == 4
    assert "sin commitear" in result.output
    assert "--allow-dirty" in result.output


def test_the_cli_measures_a_dirty_checkout_when_told_to(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentarium.benchmarks import identity as identity_module
    from agentarium.cli import app

    _cli_env(tmp_path, monkeypatch)
    monkeypatch.setattr(identity_module, "git_commit", lambda *_: ("c" * 40, True))

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "run",
            "--suite",
            "sucia",
            "--model",
            "mock",
            "--dry-run",
            "--allow-dirty",
        ],
    )

    assert result.exit_code == 0, result.output
    # And the record will say so, so it never compares equal to a clean run.
    assert '"agentarium_dirty": true' in result.output
