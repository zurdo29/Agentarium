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
    FailureCategory,
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
) -> None:
    ledger = BenchmarkLedger(tmp_path / "ledger.jsonl")
    runner = BenchmarkRunner(service, ledger)

    records = await runner.execute(
        plan_matrix([_case()], [ModelTarget.parse("mock")], 1)
    )

    assert len(records) == 1
    assert records[0].runtime_identity.agentarium_commit
    # mock has no weights.
    assert records[0].model_digest is None
