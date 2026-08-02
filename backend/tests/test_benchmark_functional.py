"""P1.1b: the validator that tells working code from convincing code.

Every case is measured against the same fixed contract — a declared
entrypoint, a declared argument list, a known fixture and an expected JSON
structure. Nothing is discovered by glob, so the result cannot depend on the
order of files in a delivery.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agentarium.benchmarks import FunctionalCheck, load_cases, run_functional_check
from agentarium.benchmarks.cases import fixtures_root
from agentarium.config.settings import project_root
from agentarium.execution.safe_commands import SafeCommandExecutor
from pydantic import ValidationError

WORKING_SCRIPT = """\
import argparse
import csv
import json
from collections import defaultdict

parser = argparse.ArgumentParser()
parser.add_argument("entrada")
parser.add_argument("--output", required=True)
opciones = parser.parse_args()

por_mes = defaultdict(float)
por_categoria = defaultdict(float)
with open(opciones.entrada, newline="", encoding="utf-8") as archivo:
    for fila in csv.DictReader(archivo):
        monto = float(fila["monto"])
        por_mes[fila["fecha"][:7]] += monto
        por_categoria[fila["categoria"]] += monto

resumen = {
    "por_mes": {clave: round(valor, 2) for clave, valor in por_mes.items()},
    "por_categoria": {clave: round(valor, 2) for clave, valor in por_categoria.items()},
}
with open(opciones.output, "w", encoding="utf-8") as salida:
    json.dump(resumen, salida)
"""

WRONG_SCRIPT = WORKING_SCRIPT.replace(
    'por_mes[fila["fecha"][:7]] += monto',
    'por_mes[fila["fecha"][:4]] += monto',
)

HANGING_SCRIPT = """\
import time

time.sleep(30)
"""

CRASHING_SCRIPT = """\
raise SystemExit("no puedo procesar el archivo")
"""


@pytest.fixture()
def executor(tmp_path: Path) -> SafeCommandExecutor:
    (tmp_path / "workspaces").mkdir()
    return SafeCommandExecutor(
        tmp_path / "workspaces",
        project_root() / "configs" / "policies" / "security.yaml",
    )


def _csv_case_check() -> FunctionalCheck:
    case = next(one for one in load_cases() if one.id == "csv_expenses_cli")
    assert case.functional is not None
    return case.functional


async def _run(
    check: FunctionalCheck,
    delivery: Path,
    executor: SafeCommandExecutor,
    *,
    python_executable: str = sys.executable,
):  # type: ignore[no-untyped-def]
    return await run_functional_check(
        check,
        delivery_root=delivery,
        fixtures_root=fixtures_root("csv_expenses_cli"),
        workspace_root=executor.workspace_root,
        executor=executor,
        python_executable=python_executable,
    )


def _delivery(tmp_path: Path, script: str, name: str = "expenses.py") -> Path:
    delivery = tmp_path / "delivery"
    delivery.mkdir(exist_ok=True)
    (delivery / name).write_text(script, encoding="utf-8")
    return delivery


# --- the contract itself ----------------------------------------------------


def test_the_csv_case_declares_the_exact_cli_contract() -> None:
    check = _csv_case_check()

    assert check.entrypoint == "expenses.py"
    assert check.args == ["input.csv", "--output", "result.json"]
    assert check.produces == "result.json"
    assert check.fixtures == {"expenses_input.csv": "input.csv"}


def test_the_csv_fixture_is_versioned_next_to_the_case() -> None:
    assert (fixtures_root("csv_expenses_cli") / "expenses_input.csv").is_file()


def test_a_functional_check_cannot_reach_outside_the_run_directory() -> None:
    base = {
        "description": "D",
        "entrypoint": "expenses.py",
        "fixtures": {"a.csv": "input.csv"},
        "produces": "result.json",
        "expect": {"a": 1},
    }
    for field, value in [
        ("entrypoint", "../fuera.py"),
        ("produces", "/etc/passwd"),
    ]:
        with pytest.raises(ValidationError):
            FunctionalCheck.model_validate({**base, field: value})
    with pytest.raises(ValidationError):
        FunctionalCheck.model_validate({**base, "fixtures": {"../a.csv": "input.csv"}})


# --- positive ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_correct_program_passes(tmp_path: Path, executor: SafeCommandExecutor) -> None:
    outcome = await _run(
        _csv_case_check(), _delivery(tmp_path, WORKING_SCRIPT), executor
    )

    assert outcome.passed, outcome.detail
    assert "esperada" in outcome.detail


@pytest.mark.asyncio
async def test_the_run_directory_is_removed_afterwards(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    await _run(_csv_case_check(), _delivery(tmp_path, WORKING_SCRIPT), executor)

    assert not list(executor.workspace_root.glob("benchmark-run-*"))


# --- wrong output -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_program_that_computes_the_wrong_answer_fails(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # Groups by year instead of by month: runs fine, produces valid JSON, and
    # is still wrong. Only a functional comparison catches this.
    outcome = await _run(_csv_case_check(), _delivery(tmp_path, WRONG_SCRIPT), executor)

    assert not outcome.passed
    assert "no coincide" in outcome.detail


@pytest.mark.asyncio
async def test_a_program_that_crashes_fails(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    outcome = await _run(
        _csv_case_check(), _delivery(tmp_path, CRASHING_SCRIPT), executor
    )

    assert not outcome.passed
    assert "código" in outcome.detail


@pytest.mark.asyncio
async def test_a_delivery_without_the_declared_entrypoint_fails(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # The exact ADR 0016 shape: the delivery documents a program it never wrote,
    # or wrote under another name. The contract names the entrypoint.
    outcome = await _run(
        _csv_case_check(),
        _delivery(tmp_path, WORKING_SCRIPT, name="gastos.py"),
        executor,
    )

    assert not outcome.passed
    assert "no incluye expenses.py" in outcome.detail


@pytest.mark.asyncio
async def test_a_shipped_result_file_is_not_credited(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # A delivery that includes result.json but no working program must fail:
    # the staged copy deletes it before running.
    delivery = _delivery(tmp_path, CRASHING_SCRIPT)
    (delivery / "result.json").write_text(
        '{"por_mes": {"2026-01": 35.75, "2026-02": 72.25, "2026-03": 90.25},'
        ' "por_categoria": {"comida": 110.0, "transporte": 13.0, "servicios": 75.25}}',
        encoding="utf-8",
    )

    outcome = await _run(_csv_case_check(), delivery, executor)

    assert not outcome.passed


# --- timeout ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_program_that_hangs_is_stopped_by_the_timeout(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    check = _csv_case_check().model_copy(update={"timeout_seconds": 1})

    outcome = await _run(check, _delivery(tmp_path, HANGING_SCRIPT), executor)

    assert not outcome.passed
    assert "superó 1s" in outcome.detail


# --- rejected command -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_executable_outside_the_allowlist_is_rejected(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    outcome = await _run(
        _csv_case_check(),
        _delivery(tmp_path, WORKING_SCRIPT),
        executor,
        python_executable="curl",
    )

    assert not outcome.passed
    assert "comando rechazado" in outcome.detail


@pytest.mark.asyncio
async def test_a_denied_token_in_the_arguments_is_rejected(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    check = _csv_case_check().model_copy(update={"args": ["input.csv", "del"]})

    outcome = await _run(check, _delivery(tmp_path, WORKING_SCRIPT), executor)

    assert not outcome.passed
    assert "comando rechazado" in outcome.detail


# --- local modules yes, third-party no --------------------------------------


HELPER_MODULE = """\
def totales(filas):
    from collections import defaultdict

    por_mes = defaultdict(float)
    por_categoria = defaultdict(float)
    for fila in filas:
        monto = float(fila["monto"])
        por_mes[fila["fecha"][:7]] += monto
        por_categoria[fila["categoria"]] += monto
    return por_mes, por_categoria
"""

SPLIT_SCRIPT = """\
import argparse
import csv
import json

import helpers

parser = argparse.ArgumentParser()
parser.add_argument("entrada")
parser.add_argument("--output", required=True)
opciones = parser.parse_args()

with open(opciones.entrada, newline="", encoding="utf-8") as archivo:
    por_mes, por_categoria = helpers.totales(list(csv.DictReader(archivo)))

resumen = {
    "por_mes": {clave: round(valor, 2) for clave, valor in por_mes.items()},
    "por_categoria": {clave: round(valor, 2) for clave, valor in por_categoria.items()},
}
with open(opciones.output, "w", encoding="utf-8") as salida:
    json.dump(resumen, salida)
"""

THIRD_PARTY_SCRIPT = """\
import pydantic

print(pydantic.__version__)
"""


@pytest.mark.asyncio
async def test_a_delivery_split_into_local_modules_passes(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # Splitting the work into `expenses.py` + `helpers.py` is good
    # organisation, not a failure. `-I` used to break exactly this.
    delivery = _delivery(tmp_path, SPLIT_SCRIPT)
    (delivery / "helpers.py").write_text(HELPER_MODULE, encoding="utf-8")

    outcome = await _run(_csv_case_check(), delivery, executor)

    assert outcome.passed, outcome.detail


@pytest.mark.asyncio
async def test_a_delivery_that_reaches_for_a_third_party_package_fails(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # `pydantic` is installed in this environment; the stdlib-only contract
    # means the run must not see it.
    outcome = await _run(
        _csv_case_check(), _delivery(tmp_path, THIRD_PARTY_SCRIPT), executor
    )

    assert not outcome.passed
    assert "pydantic" in outcome.detail.casefold()


# --- infrastructure failures are recorded, never fatal ----------------------


@pytest.mark.asyncio
async def test_a_filesystem_failure_is_reported_as_infrastructure(
    tmp_path: Path,
    executor: SafeCommandExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil as shutil_module

    def broken_copytree(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil_module, "copytree", broken_copytree)

    outcome = await _run(
        _csv_case_check(), _delivery(tmp_path, WORKING_SCRIPT), executor
    )

    assert not outcome.passed
    assert outcome.infrastructure
    assert "no se pudo preparar" in outcome.detail


@pytest.mark.asyncio
async def test_a_process_that_cannot_start_is_reported_as_infrastructure(
    tmp_path: Path,
    executor: SafeCommandExecutor,
) -> None:
    # Allowed by name, absent on disk: the token gate passes and the spawn
    # fails, which is the machine's problem and not the delivery's.
    outcome = await _run(
        _csv_case_check(),
        _delivery(tmp_path, WORKING_SCRIPT),
        executor,
        python_executable=str(tmp_path / "no-existe" / "python.exe"),
    )

    assert not outcome.passed
    assert outcome.infrastructure
    assert "no se pudo iniciar el proceso" in outcome.detail
