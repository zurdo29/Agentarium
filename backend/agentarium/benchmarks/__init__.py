"""Reproducible benchmark suite: versioned cases, one taxonomy, resumable runs."""

from .cases import InvalidBenchmarkCase, cases_root, fixtures_root, load_case, load_cases
from .contracts import (
    CASE_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkRunRecord,
    CaseValidator,
    ValidatorOutcome,
)
from .functional import FunctionalCheck, FunctionalOutcome, run_functional_check
from .gates import GateResults, gate_results
from .identity import (
    DirtyCheckout,
    MissingModelDigest,
    RuntimeIdentity,
    assert_clean,
    capture_identity,
    git_commit,
    ollama_model_digests,
    ollama_version,
)
from .ledger import BenchmarkLedger, SuiteDrift, dump_json_report
from .report import build_payload, render_markdown
from .runner import BenchmarkRunner, ModelTarget, PlannedRun, plan_matrix, prompt_versions
from .taxonomy import Classification, FailureCategory, classify

__all__ = [
    "BenchmarkCase",
    "BenchmarkLedger",
    "BenchmarkRunRecord",
    "BenchmarkRunner",
    "CASE_SCHEMA_VERSION",
    "CaseValidator",
    "Classification",
    "FailureCategory",
    "FunctionalCheck",
    "FunctionalOutcome",
    "DirtyCheckout",
    "GateResults",
    "MissingModelDigest",
    "RuntimeIdentity",
    "InvalidBenchmarkCase",
    "LEDGER_SCHEMA_VERSION",
    "ModelTarget",
    "PlannedRun",
    "SuiteDrift",
    "ValidatorOutcome",
    "build_payload",
    "cases_root",
    "classify",
    "fixtures_root",
    "assert_clean",
    "capture_identity",
    "gate_results",
    "git_commit",
    "ollama_model_digests",
    "ollama_version",
    "run_functional_check",
    "dump_json_report",
    "load_case",
    "load_cases",
    "plan_matrix",
    "prompt_versions",
    "render_markdown",
]
