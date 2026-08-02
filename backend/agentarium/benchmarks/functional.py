"""Running the delivered program against a known fixture.

A validator that only looks for files measures whether a model *named* things
correctly. This one runs the delivery on a fixture whose right answer is known
and compares parsed JSON, which is the only way a benchmark can tell working
code from convincing code.

**Trust boundary.** This reuses exactly the boundary `SCRIPT_EXECUTION`
already uses: `SafeCommandExecutor` constrains the executable, the argument
list, the environment and the working directory, and there is no shell. It is
*not* an OS-level sandbox — the delivered script still runs as this user and
could reach absolute paths, the network or spawn processes. Nothing here adds
isolation, and nothing here should be described as if it did. A real sandbox
is a separate architectural decision.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentarium.execution.safe_commands import CommandRejected, SafeCommandExecutor


def _reject_escaping(value: str, field: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError(f"{field} cannot be blank")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        raise ValueError(f"{field} must be a safe relative path: {value!r}")
    return normalized


class FunctionalCheck(BaseModel):
    """The exact contract every model is measured against for this case."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    # A list, never a shell string: the executor takes argv, so nothing here
    # can be interpreted as a pipeline, a redirection or a second command.
    args: list[str] = Field(default_factory=list, max_length=20)
    fixtures: dict[str, str] = Field(min_length=1)
    produces: str = Field(min_length=1)
    expect: dict[str, Any] = Field(min_length=1)
    timeout_seconds: int = Field(default=15, ge=1, le=120)

    @field_validator("entrypoint", "produces")
    @classmethod
    def validate_relative(cls, value: str) -> str:
        return _reject_escaping(value, "path")

    @field_validator("fixtures")
    @classmethod
    def validate_fixture_paths(cls, values: dict[str, str]) -> dict[str, str]:
        return {
            _reject_escaping(source, "fixture source"): _reject_escaping(
                target, "fixture target"
            )
            for source, target in values.items()
        }

    @field_validator("args")
    @classmethod
    def reject_blank_args(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("args cannot contain blank entries")
        return values


@dataclass(frozen=True)
class FunctionalOutcome:
    passed: bool
    detail: str
    # The machine failed, not the delivery. The run still has to be recorded
    # and the matrix has to continue: 27 runs cannot die because one temp
    # directory could not be copied.
    infrastructure: bool = False


async def run_functional_check(
    check: FunctionalCheck,
    *,
    delivery_root: Path,
    fixtures_root: Path,
    workspace_root: Path,
    executor: SafeCommandExecutor,
    python_executable: str,
) -> FunctionalOutcome:
    """Copy delivery + fixtures somewhere disposable, run, compare, clean up.

    Filesystem work goes through `asyncio.to_thread`, the same way
    `SafeCommandExecutor` handles its own blocking calls.
    """
    # Inside workspace_root because SafeCommandExecutor refuses to run anywhere
    # else, and disposable so a run never observes another run's leftovers.
    run_root = workspace_root / f"benchmark-run-{uuid4().hex}"
    try:
        try:
            problem = await asyncio.to_thread(
                _prepare_run, check, delivery_root, fixtures_root, run_root
            )
        except OSError as exc:
            # Copying the delivery or a fixture can fail for reasons that have
            # nothing to do with the model being measured.
            return FunctionalOutcome(
                False,
                f"no se pudo preparar la ejecución: {exc}",
                infrastructure=True,
            )
        if problem is not None:
            return FunctionalOutcome(False, problem)

        command = [
            python_executable,
            # NOT `-I`: isolated mode also drops the script's own directory
            # from sys.path, so a delivery that splits itself into
            # `expenses.py` + `helpers.py` — perfectly good organisation —
            # would fail on the import instead of on its logic. These four do
            # what the case actually needs: ignore PYTHON* env vars (-E), skip
            # the user site directory (-s), skip `site` entirely so
            # site-packages never joins sys.path (-S), and leave no bytecode
            # behind (-B). Local imports keep working; third-party ones do not,
            # which is the stdlib-only contract.
            "-E",
            "-s",
            "-S",
            "-B",
            check.entrypoint,
            *check.args,
        ]
        try:
            result = await executor.execute(
                command,
                cwd=run_root,
                timeout_seconds=check.timeout_seconds,
            )
        except CommandRejected as exc:
            return FunctionalOutcome(False, f"comando rechazado: {exc}")
        except OSError as exc:
            # The process could not even be started (missing interpreter,
            # exhausted handles). Not the delivery's fault.
            return FunctionalOutcome(
                False,
                f"no se pudo iniciar el proceso: {exc}",
                infrastructure=True,
            )

        if result.timed_out:
            return FunctionalOutcome(
                False,
                f"la ejecución superó {check.timeout_seconds}s y se detuvo",
            )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "").strip()[:400]
            return FunctionalOutcome(
                False,
                f"salió con código {result.return_code}: {detail or 'sin salida'}",
            )
        try:
            return await asyncio.to_thread(_compare_output, check, run_root)
        except OSError as exc:
            return FunctionalOutcome(
                False,
                f"no se pudo leer la salida: {exc}",
                infrastructure=True,
            )
    finally:
        # ignore_errors: cleanup must never be the reason a run is lost.
        await asyncio.to_thread(shutil.rmtree, run_root, True)


def _prepare_run(
    check: FunctionalCheck,
    delivery_root: Path,
    fixtures_root: Path,
    run_root: Path,
) -> str | None:
    """Stage the run directory. Returns a failure detail, or None if ready."""
    if not delivery_root.is_dir():
        return "no se materializó ninguna entrega"
    if not (delivery_root / check.entrypoint).is_file():
        # The contract names the entrypoint; a delivery without it fails the
        # case regardless of what else it produced.
        return f"la entrega no incluye {check.entrypoint}"

    shutil.copytree(delivery_root, run_root)
    for source, target in check.fixtures.items():
        origin = fixtures_root / source
        if not origin.is_file():
            return f"falta el fixture {source}"
        destination = run_root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)

    # A delivery that ships the answer must not be credited for it.
    (run_root / check.produces).unlink(missing_ok=True)
    return None


def _compare_output(check: FunctionalCheck, run_root: Path) -> FunctionalOutcome:
    produced = run_root / check.produces
    if not produced.is_file():
        return FunctionalOutcome(False, f"no se generó {check.produces}")
    try:
        actual = json.loads(produced.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return FunctionalOutcome(False, f"{check.produces} no es JSON válido: {exc}")

    # Structural comparison, never text: key order and whitespace are not part
    # of the contract.
    if actual != check.expect:
        rendered = json.dumps(actual, ensure_ascii=False)[:400]
        return FunctionalOutcome(False, f"la salida no coincide con la esperada: {rendered}")
    return FunctionalOutcome(True, "la entrega produjo la salida esperada")
