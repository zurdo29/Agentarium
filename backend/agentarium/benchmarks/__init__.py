"""Reproducible benchmark suite: versioned cases, one taxonomy, resumable runs."""

from .cases import InvalidBenchmarkCase, cases_root, load_case, load_cases
from .contracts import (
    CASE_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkRunRecord,
    CaseValidator,
    ValidatorOutcome,
)
from .ledger import BenchmarkLedger, dump_json_report
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
    "InvalidBenchmarkCase",
    "LEDGER_SCHEMA_VERSION",
    "ModelTarget",
    "PlannedRun",
    "ValidatorOutcome",
    "build_payload",
    "cases_root",
    "classify",
    "dump_json_report",
    "load_case",
    "load_cases",
    "plan_matrix",
    "prompt_versions",
    "render_markdown",
]
