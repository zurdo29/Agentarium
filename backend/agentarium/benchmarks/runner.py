"""Driving the matrix: one project per (case, model, repetition), resumable."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from agentarium.llm import (
    DECOMPOSE_PROMPT_VERSION,
    PLAN_REVISION_PROMPT_VERSION,
    PLANNING_PROMPT_VERSION,
    WORKSPACE_PROMPT_VERSION,
)
from agentarium.services import ApplicationService

from .cases import fixtures_root
from .contracts import BenchmarkCase, BenchmarkRunRecord, ValidatorOutcome
from .functional import run_functional_check
from .gates import gate_results
from .identity import (
    MissingModelDigest,
    RuntimeIdentity,
    assert_clean,
    capture_identity,
    ollama_model_digests,
    ollama_version,
)
from .ledger import BenchmarkLedger, SuiteDrift
from .taxonomy import Classification, FailureCategory, classify


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider

    @classmethod
    def parse(cls, raw: str) -> ModelTarget:
        provider, _, model = raw.partition(":")
        provider = provider.strip()
        if not provider:
            raise ValueError(f"Objetivo de modelo inválido: {raw!r}")
        return cls(provider=provider, model=model.strip())


@dataclass(frozen=True)
class PlannedRun:
    case: BenchmarkCase
    target: ModelTarget
    repetition: int

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.case.id, self.target.provider, self.target.model, self.repetition)


def plan_matrix(
    cases: list[BenchmarkCase],
    targets: list[ModelTarget],
    repetitions: int,
) -> list[PlannedRun]:
    return [
        PlannedRun(case=case, target=target, repetition=repetition)
        for case in cases
        for target in targets
        for repetition in range(1, repetitions + 1)
    ]


def prompt_versions() -> dict[str, str]:
    return {
        "planning": PLANNING_PROMPT_VERSION,
        "workspace": WORKSPACE_PROMPT_VERSION,
        "decompose": DECOMPOSE_PROMPT_VERSION,
        "plan_revision": PLAN_REVISION_PROMPT_VERSION,
    }


class BenchmarkRunner:
    def __init__(
        self,
        service: ApplicationService,
        ledger: BenchmarkLedger,
        *,
        identity: RuntimeIdentity | None = None,
        model_digests: dict[str, str] | None = None,
    ) -> None:
        self.service = service
        self.ledger = ledger
        self._identity = identity
        self._model_digests = model_digests or {}

    async def freeze_identity(self) -> None:
        """Capture the reference this invocation measures against.

        Frozen once so every record carries the same reference, and revalidated
        before each run so a mid-matrix change is caught rather than silently
        recorded run by run.
        """
        settings = self.service.settings
        self._identity = await capture_identity(settings)
        assert_clean(self._identity)
        self._model_digests = await ollama_model_digests(settings)

    def assert_digests_available(self, planned: list[PlannedRun]) -> None:
        """Every model the matrix asks for must exist before spending hours.

        A missing digest means Ollama is not running or the tag is not
        installed. Discovering that on run 14 wastes the 13 before it.
        """
        missing = sorted(
            {
                run.target.label
                for run in planned
                if run.target.provider == "ollama" and self.digest_for(run.target) is None
            }
        )
        if missing:
            raise MissingModelDigest(
                "No se pudo obtener el digest de: " + ", ".join(missing) + ". "
                "Verificá que Ollama esté corriendo y que los modelos estén "
                "instalados (`ollama list`) antes de lanzar la matriz."
            )

    async def revalidate_runtime(self, target: ModelTarget) -> None:
        """Re-probe Ollama right before a run and abort if it moved.

        The frozen identity is the reference, not a snapshot to trust for
        hours: `ollama pull` mid-matrix would otherwise be recorded as if
        nothing had changed.
        """
        if target.provider != "ollama":
            return
        settings = self.service.settings
        version = await ollama_version(settings)
        if version != self.identity.ollama_version:
            raise SuiteDrift(
                f"Ollama cambió de versión durante la matriz: "
                f"{self.identity.ollama_version} → {version}. La suite deja de "
                "ser comparable; usá --suite con un nombre nuevo."
            )
        digests = await ollama_model_digests(settings)
        current = digests.get(target.model)
        frozen = self.digest_for(target)
        if current != frozen:
            raise SuiteDrift(
                f"Las pesas de {target.label} cambiaron durante la matriz: "
                f"{frozen} → {current}. La suite deja de ser comparable; usá "
                "--suite con un nombre nuevo."
            )

    @property
    def identity(self) -> RuntimeIdentity:
        if self._identity is None:
            raise RuntimeError("freeze_identity() debe ejecutarse antes de medir")
        return self._identity

    def digest_for(self, target: ModelTarget) -> str | None:
        if target.provider != "ollama" or not target.model:
            return None
        return self._model_digests.get(target.model)

    def pending(
        self,
        planned: list[PlannedRun],
        *,
        rerun: bool = False,
    ) -> list[PlannedRun]:
        """What is left to measure. `rerun` deliberately supersedes results."""
        self.ledger.assert_comparable(
            case_versions={run.case.id: run.case.schema_version for run in planned},
            prompt_versions=prompt_versions(),
            runtime_identity=self.identity,
            model_digests={
                (run.target.provider, run.target.model): self.digest_for(run.target)
                for run in planned
            },
        )
        if rerun:
            return list(planned)
        done = self.ledger.completed_keys()
        return [run for run in planned if run.key not in done]

    async def execute(
        self,
        planned: list[PlannedRun],
        *,
        rerun: bool = False,
        on_progress: object = None,
    ) -> list[BenchmarkRunRecord]:
        if self._identity is None:
            await self.freeze_identity()
        self.assert_digests_available(planned)
        records: list[BenchmarkRunRecord] = []
        for run in self.pending(planned, rerun=rerun):
            # Before, not after: a run measured against moved weights should
            # never reach the ledger.
            await self.revalidate_runtime(run.target)
            try:
                record = await self.execute_one(run)
            except SuiteDrift:
                # Never demoted to an `infrastructure` data point: drift means
                # the matrix must stop, not carry on with a note.
                raise
            except Exception as exc:
                # Last line of defence. A matrix is hours of inference; no
                # single combination may take the rest of it down. Whatever
                # went wrong is recorded as infrastructure and the loop moves
                # on, so a resumed run does not repeat what already ran.
                detail = f"{type(exc).__name__}: {exc}"
                record = self._failed_record(
                    run,
                    time.monotonic(),
                    Classification(FailureCategory.INFRASTRUCTURE, detail),
                    error=detail,
                )
            self.ledger.append(record)
            records.append(record)
            if callable(on_progress):
                on_progress(record)
        return records

    async def execute_one(self, run: PlannedRun) -> BenchmarkRunRecord:
        started = time.monotonic()
        title = f"[benchmark] {run.case.id} · {run.target.label} · #{run.repetition}"
        try:
            self.service.roles.select_provider(run.target.provider, run.target.model or None)
        except Exception as exc:  # provider not selectable at all
            return self._failed_record(
                run,
                started,
                Classification(FailureCategory.PROVIDER_FAILURE, str(exc)),
                error=str(exc),
            )

        project_id: str | None = None
        error: str | None = None
        try:
            project = self.service.create_project(run.case.goal, title)
            project_id = project.id
            await self.service.plan_project(project.id)
            await self.service.run_project(project.id)
        except Exception as exc:
            # A benchmark must record a crash as a data point, not abort the
            # matrix that is still hours from finishing.
            error = f"{type(exc).__name__}: {exc}"
            if project_id is None:
                return self._failed_record(
                    run,
                    started,
                    Classification(FailureCategory.PROVIDER_FAILURE, error),
                    error=error,
                )

        functional = await self.run_functional(run.case, project_id)
        return self._observe(run, project_id, started, error, functional)

    async def run_functional(
        self,
        case: BenchmarkCase,
        project_id: str,
    ) -> tuple[ValidatorOutcome, bool] | None:
        """Execute the delivery against the case's fixture, if it declares one.

        Returns the outcome and whether it failed for infrastructure reasons,
        which is not something the measured model should be blamed for.
        """
        if case.functional is None:
            return None
        outcome = await run_functional_check(
            case.functional,
            delivery_root=self.delivery_root(project_id),
            fixtures_root=fixtures_root(case.id),
            workspace_root=self.service.settings.workspace_root,
            # The same executor SCRIPT_EXECUTION uses: identical trust
            # boundary, no extra isolation claimed.
            executor=self.service.orchestrator.validations.executor,
            python_executable=sys.executable,
        )
        return (
            ValidatorOutcome(
                description=case.functional.description,
                passed=outcome.passed,
                detail=outcome.detail,
            ),
            outcome.infrastructure,
        )

    def _observe(
        self,
        run: PlannedRun,
        project_id: str,
        started: float,
        error: str | None,
        functional: tuple[ValidatorOutcome, bool] | None = None,
    ) -> BenchmarkRunRecord:
        repository = self.service.repository
        project = repository.get_project(project_id)
        items = repository.list_work_items(project_id)
        events = repository.list_events(project_id)
        reviews = repository.list_reviews(project_id)
        test_reports = repository.list_test_reports(project_id)

        outcomes = self.validate_delivery(run.case, self.delivery_root(project_id))
        functional_infrastructure = False
        if functional is not None:
            functional_outcome, functional_infrastructure = functional
            outcomes.append(functional_outcome)
        validation_passed = all(outcome.passed for outcome in outcomes)
        if functional_infrastructure:
            # The check never got to measure anything, so the project's own
            # state cannot explain this run.
            classification = Classification(
                FailureCategory.INFRASTRUCTURE,
                outcomes[-1].detail,
            )
        else:
            classification = classify(
                project,
                items,
                events,
                validation_passed=validation_passed,
                reviews=reviews,
                test_reports=test_reports,
            )
        gates = gate_results(items, reviews, test_reports)

        return BenchmarkRunRecord(
            case_id=run.case.id,
            case_schema_version=run.case.schema_version,
            provider=run.target.provider,
            model=run.target.model,
            repetition=run.repetition,
            project_id=project_id,
            project_status=project.status.value,
            progress_percent=round(project.progress_percent, 2),
            duration_seconds=round(time.monotonic() - started, 2),
            attempts=sum(item.attempt_count for item in items),
            splits=sum(1 for event in events if event.get("action") == "task_split_created"),
            human_intervention=False,
            prompt_versions=prompt_versions(),
            runtime_identity=self.identity,
            model_digest=self.digest_for(run.target),
            technical_result=gates.technical,
            semantic_result=gates.semantic,
            technical_reports=gates.technical_reports,
            semantic_reviews=gates.semantic_reviews,
            validation_passed=validation_passed,
            validators=outcomes,
            category=classification.category,
            evidence=classification.evidence,
            error=error,
        )

    def delivery_root(self, project_id: str) -> Path:
        return self.service.settings.workspace_root / project_id / "project"

    @staticmethod
    def validate_delivery(case: BenchmarkCase, root: Path) -> list[ValidatorOutcome]:
        if not root.is_dir():
            return [
                ValidatorOutcome(
                    description=validator.description,
                    passed=False,
                    detail="no se materializó ninguna entrega",
                )
                for validator in case.validators
            ]
        outcomes: list[ValidatorOutcome] = []
        for validator in case.validators:
            passed, detail = validator.check(root)
            outcomes.append(
                ValidatorOutcome(
                    description=validator.description,
                    passed=passed,
                    detail=detail,
                )
            )
        return outcomes

    def _failed_record(
        self,
        run: PlannedRun,
        started: float,
        classification: Classification,
        *,
        error: str,
    ) -> BenchmarkRunRecord:
        return BenchmarkRunRecord(
            case_id=run.case.id,
            case_schema_version=run.case.schema_version,
            provider=run.target.provider,
            model=run.target.model,
            repetition=run.repetition,
            project_id=None,
            project_status="failed",
            progress_percent=0.0,
            duration_seconds=round(time.monotonic() - started, 2),
            attempts=0,
            splits=0,
            prompt_versions=prompt_versions(),
            runtime_identity=self.identity,
            model_digest=self.digest_for(run.target),
            technical_result=False,
            semantic_result=False,
            validation_passed=False,
            validators=[],
            category=classification.category,
            evidence=classification.evidence,
            error=error,
        )
