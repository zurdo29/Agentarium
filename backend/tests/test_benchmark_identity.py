"""P1.2a: a suite must know what it actually measured.

`prompt_versions` and `case_schema_version` freeze what we declare. They say
nothing about the code that ran or the weights behind a model name — and an
Ollama tag is mutable, so "the same model" can be two different models across
a matrix that takes hours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentarium.benchmarks import (
    BenchmarkLedger,
    BenchmarkRunner,
    DirtyCheckout,
    FailureCategory,
    MissingModelDigest,
    ModelTarget,
    RuntimeIdentity,
    SuiteDrift,
    capture_identity,
    git_commit,
    load_cases,
    plan_matrix,
    prompt_versions,
)
from agentarium.benchmarks.contracts import BenchmarkRunRecord
from agentarium.config.settings import Settings, project_root
from agentarium.services import ApplicationService

IDENTITY = RuntimeIdentity(
    agentarium_commit="a" * 40,
    agentarium_dirty=False,
    python_version="3.14.0",
    platform="Windows-AMD64",
    concurrency=1,
    ollama_version="0.32.5",
)


def _record(**overrides: object) -> BenchmarkRunRecord:
    payload: dict[str, object] = {
        "case_id": "demo",
        "case_schema_version": 1,
        "provider": "ollama",
        "model": "qwen3:4b",
        "repetition": 1,
        "project_id": "p",
        "project_status": "completed",
        "progress_percent": 100.0,
        "duration_seconds": 1.0,
        "attempts": 1,
        "splits": 0,
        "technical_result": True,
        "semantic_result": True,
        "validation_passed": True,
        "validators": [],
        "category": FailureCategory.COMPLETED,
        "evidence": "ok",
        "prompt_versions": prompt_versions(),
        "runtime_identity": IDENTITY.model_dump(),
        "model_digest": "sha256:aaaa",
    }
    payload.update(overrides)
    return BenchmarkRunRecord.model_validate(payload)


def _case():  # type: ignore[no-untyped-def]
    return load_cases(only=["architecture_document"])[0]


def _runner(
    service: ApplicationService,
    tmp_path: Path,
    *,
    identity: RuntimeIdentity = IDENTITY,
    digests: dict[str, str] | None = None,
) -> BenchmarkRunner:
    return BenchmarkRunner(
        service,
        BenchmarkLedger(tmp_path / "ledger.jsonl"),
        identity=identity,
        model_digests=digests if digests is not None else {"qwen3:4b": "sha256:aaaa"},
    )


# --- capturing --------------------------------------------------------------


def test_the_commit_of_this_checkout_is_captured() -> None:
    commit, dirty = git_commit()

    assert commit != "unknown"
    assert len(commit) == 40
    assert isinstance(dirty, bool)


def test_a_directory_without_git_is_reported_as_unknown_and_dirty(tmp_path: Path) -> None:
    # Recorded as such rather than guessed: it will simply never compare equal
    # to a real checkout.
    commit, dirty = git_commit(tmp_path)

    assert commit == "unknown"
    assert dirty is True


@pytest.mark.asyncio
async def test_capturing_identity_fills_every_field(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        workspace_root=tmp_path / "workspaces",
        config_root=project_root() / "configs",
        provider_state_path=tmp_path / "provider.json",
        model_concurrency=2,
        # Unreachable on purpose: Ollama being down must not break the capture.
        ollama_url="http://127.0.0.1:1",
    )

    identity = await capture_identity(settings)

    assert identity.agentarium_commit
    assert identity.python_version
    assert identity.platform
    assert identity.concurrency == 2
    assert identity.ollama_version is None


def test_the_identity_is_part_of_the_serialized_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    BenchmarkLedger(path).append(_record())

    raw = json.loads(path.read_text(encoding="utf-8").strip())
    assert raw["runtime_identity"]["agentarium_commit"] == "a" * 40
    assert raw["model_digest"] == "sha256:aaaa"


# --- drift ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agentarium_commit", "b" * 40),
        ("agentarium_dirty", True),
        ("python_version", "3.13.0"),
        ("platform", "Linux-x86_64"),
        ("concurrency", 4),
        ("ollama_version", "0.33.0"),
    ],
)
def test_any_runtime_change_refuses_to_reuse_the_suite(
    service: ApplicationService,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record())
    changed = IDENTITY.model_copy(update={field: value})
    runner = _runner(service, tmp_path, identity=changed)

    with pytest.raises(SuiteDrift, match="runtime"):
        runner.pending(plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:4b")], 1))


def test_a_repulled_model_tag_refuses_to_reuse_the_suite(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    # `ollama pull qwen3:4b` can replace the weights without the tag changing.
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record())
    runner = _runner(service, tmp_path, digests={"qwen3:4b": "sha256:bbbb"})

    with pytest.raises(SuiteDrift, match="digest"):
        runner.pending(plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:4b")], 1))


def test_an_unchanged_environment_stays_comparable(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record(case_id="architecture_document"))
    runner = _runner(service, tmp_path)

    pending = runner.pending(
        plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:4b")], 1)
    )

    assert pending == []


def test_a_digest_for_another_model_does_not_trip_the_comparison(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    # Digests are compared per (provider, model): a matrix that adds a second
    # model must not invalidate the first one's records.
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record(case_id="architecture_document"))
    runner = _runner(
        service,
        tmp_path,
        digests={"qwen3:4b": "sha256:aaaa", "qwen3:8b": "sha256:cccc"},
    )

    pending = runner.pending(
        plan_matrix(
            [_case()],
            [ModelTarget.parse("ollama:qwen3:4b"), ModelTarget.parse("ollama:qwen3:8b")],
            1,
        )
    )

    assert [run.target.model for run in pending] == ["qwen3:8b"]


def test_measuring_before_freezing_the_identity_is_refused(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    runner = BenchmarkRunner(service, BenchmarkLedger(tmp_path / "ledger.jsonl"))

    with pytest.raises(RuntimeError, match="freeze_identity"):
        runner.pending(plan_matrix([_case()], [ModelTarget.parse("mock")], 1))


@pytest.mark.asyncio
async def test_execute_freezes_the_identity_on_its_own(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentarium.benchmarks import identity as identity_module

    # This checkout is usually dirty while developing; the test is about the
    # freezing, not about the state of the tree.
    monkeypatch.setattr(identity_module, "git_commit", lambda *_: ("d" * 40, False))
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = BenchmarkRunner(service, ledger)

    records = await runner.execute(
        plan_matrix([_case()], [ModelTarget.parse("mock")], 1)
    )

    assert len(records) == 1
    assert records[0].runtime_identity.agentarium_commit == "d" * 40
    # mock has no weights.
    assert records[0].model_digest is None


# --- revalidation before every run ------------------------------------------


@pytest.mark.asyncio
async def test_weights_that_change_between_runs_abort_the_matrix(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ollama pull` mid-matrix must stop it, not be recorded as if nothing moved."""
    from agentarium.benchmarks import runner as runner_module

    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = _runner(service, tmp_path)
    target = ModelTarget.parse("ollama:qwen3:4b")
    planned = plan_matrix([_case()], [target], 2)

    probes = iter([{"qwen3:4b": "sha256:aaaa"}, {"qwen3:4b": "sha256:bbbb"}])

    async def shifting_digests(_settings):  # type: ignore[no-untyped-def]
        return next(probes)

    async def steady_version(_settings):  # type: ignore[no-untyped-def]
        return "0.32.5"

    async def fake_run(self, run):  # type: ignore[no-untyped-def]
        return _record(repetition=run.repetition, case_id=run.case.id)

    monkeypatch.setattr(runner_module, "ollama_model_digests", shifting_digests)
    monkeypatch.setattr(runner_module, "ollama_version", steady_version)
    monkeypatch.setattr(BenchmarkRunner, "execute_one", fake_run)

    with pytest.raises(SuiteDrift, match="pesas"):
        await runner.execute(planned)

    # The first run is preserved; the second never reached the ledger.
    assert len(ledger.records()) == 1
    assert ledger.records()[0].repetition == 1


@pytest.mark.asyncio
async def test_an_ollama_version_change_between_runs_aborts_the_matrix(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentarium.benchmarks import runner as runner_module

    runner = _runner(service, tmp_path)
    planned = plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:4b")], 1)

    async def steady_digests(_settings):  # type: ignore[no-untyped-def]
        return {"qwen3:4b": "sha256:aaaa"}

    async def upgraded(_settings):  # type: ignore[no-untyped-def]
        return "0.33.0"

    monkeypatch.setattr(runner_module, "ollama_model_digests", steady_digests)
    monkeypatch.setattr(runner_module, "ollama_version", upgraded)

    with pytest.raises(SuiteDrift, match="versión"):
        await runner.execute(planned)


@pytest.mark.asyncio
async def test_a_mock_target_is_not_revalidated_against_ollama(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    # A mock-only matrix must not abort because Ollama happens to be down, or up.
    runner = _runner(service, tmp_path, digests={})

    records = await runner.execute(
        plan_matrix([_case()], [ModelTarget.parse("mock")], 1)
    )

    assert len(records) == 1


# --- missing models fail early ----------------------------------------------


def test_a_model_that_is_not_installed_fails_before_measuring(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    runner = _runner(service, tmp_path, digests={"qwen3:4b": "sha256:aaaa"})
    planned = plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:8b")], 3)

    with pytest.raises(MissingModelDigest, match="qwen3:8b"):
        runner.assert_digests_available(planned)


def test_ollama_being_absent_fails_before_measuring(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    # No digests at all: the server is not answering.
    runner = _runner(service, tmp_path, digests={})
    planned = plan_matrix(
        [_case()],
        [ModelTarget.parse("ollama:qwen3:4b"), ModelTarget.parse("ollama:qwen3:8b")],
        3,
    )

    with pytest.raises(MissingModelDigest) as error:
        runner.assert_digests_available(planned)

    assert "qwen3:4b" in str(error.value)
    assert "qwen3:8b" in str(error.value)


@pytest.mark.asyncio
async def test_the_matrix_refuses_to_start_with_a_missing_model(
    service: ApplicationService,
    tmp_path: Path,
) -> None:
    runner = _runner(service, tmp_path, digests={})
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")

    with pytest.raises(MissingModelDigest):
        await runner.execute(
            plan_matrix([_case()], [ModelTarget.parse("ollama:qwen3:4b")], 3)
        )

    assert ledger.records() == []


# --- the ledger format ------------------------------------------------------


def test_a_v1_ledger_is_rejected(tmp_path: Path) -> None:
    # v1 predates runtime_identity and model_digest: its records cannot say
    # what they measured, so they must not be read into today's fields.
    path = tmp_path / "ledger.jsonl"
    payload = _record().model_dump(mode="json")
    payload["schema_version"] = 1
    payload.pop("runtime_identity")
    payload.pop("model_digest")
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="línea 1"):
        BenchmarkLedger(path).records()


def test_the_current_ledger_declares_version_two(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    BenchmarkLedger(path).append(_record())

    assert json.loads(path.read_text(encoding="utf-8").strip())["schema_version"] == 2


# --- a dirty checkout is refused outright -----------------------------------


@pytest.mark.asyncio
async def test_freezing_the_identity_refuses_a_dirty_checkout(
    service: ApplicationService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No escape hatch: a boolean cannot tell two different dirty trees apart,
    # so two such runs would compare equal while measuring different code.
    from agentarium.benchmarks import identity as identity_module

    monkeypatch.setattr(identity_module, "git_commit", lambda *_: ("c" * 40, True))
    runner = BenchmarkRunner(service, BenchmarkLedger(tmp_path / "ledger.jsonl"))

    with pytest.raises(DirtyCheckout, match="sin commitear"):
        await runner.freeze_identity()
