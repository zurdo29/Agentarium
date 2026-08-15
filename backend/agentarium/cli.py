from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from agentarium.config.settings import Settings, get_settings, project_root
from agentarium.isolation import ExportError, ImportSourceError
from agentarium.llm import ProviderDiagnostic, ProviderRegistry, ProviderSelectionStore
from agentarium.repositories.backup import (
    BackupValidationError,
    MigrationFailedError,
    RestoreError,
    sqlite_path_from_url,
)
from agentarium.repositories.backup import (
    restore as restore_database_file,
)
from agentarium.repositories.migrations import SchemaTooNewError
from agentarium.services import ApplicationService, build_application

# Distinct, stable exit codes for the three ways `Database.create_all()` can
# refuse to start: each points at a different fix (retry/restore vs. upgrade
# the code vs. a bad backup, see docs/decisions/0032-*.md).
_EXIT_MIGRATION_FAILED = 6
_EXIT_SCHEMA_TOO_NEW = 7
_EXIT_BACKUP_INVALID = 9
_EXIT_RESTORE_FAILED = 8
# `doctor` exits non-zero only when at least one check is a hard `fail` --
# see docs/guides/windows-setup.md for what each check means.
_EXIT_DOCTOR_FAILED = 12

app = typer.Typer(
    name="agentarium",
    help="Empresa local de agentes con artefactos verificables.",
    no_args_is_help=True,
)
project_app = typer.Typer(help="Crear y controlar proyectos.", no_args_is_help=True)
app.add_typer(project_app, name="project")
benchmark_app = typer.Typer(
    help="Medir el sistema con casos versionados.",
    no_args_is_help=True,
)
app.add_typer(benchmark_app, name="benchmark")
db_app = typer.Typer(help="Mantenimiento de la base de datos.", no_args_is_help=True)
app.add_typer(db_app, name="db")
repair_app = typer.Typer(help="Ver y reparar tareas fallidas.", no_args_is_help=True)
app.add_typer(repair_app, name="repair")


_StartupFailure = MigrationFailedError | SchemaTooNewError | BackupValidationError


def _exit_on_startup_failure(exc: _StartupFailure) -> NoReturn:
    typer.echo(str(exc), err=True)
    if isinstance(exc, MigrationFailedError):
        raise typer.Exit(_EXIT_MIGRATION_FAILED) from exc
    if isinstance(exc, SchemaTooNewError):
        raise typer.Exit(_EXIT_SCHEMA_TOO_NEW) from exc
    raise typer.Exit(_EXIT_BACKUP_INVALID) from exc


def _service() -> ApplicationService:
    # Not .initialize(): that runs a global crash-recovery sweep, which
    # would yank any work item back to READY out from under a `project run`
    # that's actively mid-flight in another process. Schema/directories
    # only here; recovery stays scoped to genuine startups (`init`, the API
    # server) or to the specific project a `run` is about to drive.
    service = build_application()
    try:
        service.ensure_ready()
    except (MigrationFailedError, SchemaTooNewError, BackupValidationError) as exc:
        _exit_on_startup_failure(exc)
    return service


@dataclass(frozen=True)
class _DoctorCheck:
    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str
    hint: str | None = None


# GitWorktreeIsolation.prepare()'s worst-case suffix under workspace_root is
# project_id(36) + "\worktrees\"(11) + leaf(~22) ~= 69 chars. ADR 0022
# measured 194 total chars working and 230 failing for git's own internal
# worktree bookkeeping on Windows ("fatal: '$GIT_DIR' too big") -- a limit
# core.longpaths does not cover, since that only affects the working-tree
# checkout. A 100-char cap on workspace_root alone leaves real paths at
# roughly 169, comfortably under the known-good 194.
_WORKSPACE_PATH_WARN_THRESHOLD = 100


def _active_selection(settings: Settings) -> tuple[str, str | None]:
    """Same precedence `build_application()` uses (services/application.py),
    without going through it: `RoleCatalog.select_provider()` raises an
    uncaught `ValueError` for a non-mock provider with no model, and doctor
    has to stay usable to diagnose exactly that misconfiguration."""
    saved = ProviderSelectionStore(settings.provider_state_path).load()
    if saved is not None:
        return saved.provider, saved.model
    return settings.provider, (settings.model or None)


def _tool_check(name: str, command: list[str], *, required: bool) -> _DoctorCheck:
    version = _version(command)
    if version is not None:
        return _DoctorCheck(name, "pass", version)
    if not required:
        return _DoctorCheck(name, "pass", "no encontrado (no requerido)")
    return _DoctorCheck(
        name,
        "fail",
        "no encontrado",
        f"Instalá '{command[0]}' y asegurate de que esté en PATH.",
    )


def _provider_checks(
    diagnostics: list[ProviderDiagnostic],
    active_provider: str,
    active_model: str | None,
) -> list[_DoctorCheck]:
    model_ok = active_provider == "mock" or bool(active_model)
    checks = [
        _DoctorCheck(
            "provider_config",
            "pass" if model_ok else "fail",
            f"{active_provider} / {active_model or '(sin modelo)'}",
            None
            if model_ok
            else (
                "Un proveedor distinto de mock necesita AGENTARIUM_MODEL "
                "(o un modelo elegido desde la interfaz web) -- sin esto, "
                "cualquier comando falla con un error sin capturar."
            ),
        )
    ]
    for diagnostic in diagnostics:
        is_active = diagnostic.name == active_provider
        if not diagnostic.ready:
            status = "fail" if is_active else "warn"
        elif is_active and active_model and active_model not in diagnostic.models:
            status = "fail"
        else:
            status = "pass"
        hint: str | None = None
        if status == "fail" and is_active:
            hint = diagnostic.message
        elif status == "warn":
            hint = f"No es el proveedor activo ({active_provider}); no bloquea nada."
        checks.append(_DoctorCheck(diagnostic.name, status, diagnostic.message, hint))
    return checks


def _workspace_path_check(workspace_root: Path) -> _DoctorCheck:
    resolved = str(workspace_root.resolve())
    if len(resolved) <= _WORKSPACE_PATH_WARN_THRESHOLD:
        return _DoctorCheck("workspace_path", "pass", resolved)
    return _DoctorCheck(
        "workspace_path",
        "warn",
        resolved,
        "Ruta larga: 'git worktree add' puede fallar en Windows con "
        "\"fatal: '$GIT_DIR' too big\". Configurá AGENTARIUM_WORKSPACE_ROOT "
        "a una ruta más corta -- ver docs/guides/windows-setup.md.",
    )


def _writability_check(name: str, directory: Path, hint: str) -> _DoctorCheck:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle, marker_path = tempfile.mkstemp(dir=directory, prefix=".agentarium-doctor-")
        os.close(handle)
        os.unlink(marker_path)
    except OSError as exc:
        return _DoctorCheck(name, "fail", str(exc), hint)
    return _DoctorCheck(name, "pass", str(directory))


@app.command()
def doctor() -> None:
    """Comprueba herramientas, almacenamiento y proveedor local."""
    # Settings() directo, nunca get_settings(): get_settings() llama
    # ensure_directories() (mkdir de workspace/DB/provider-state) -- si eso
    # falla, doctor explotaría antes de poder diagnosticar nada, exactamente
    # lo opuesto de su propósito. _writability_check hace su propio mkdir,
    # envuelto en try/except, en vez de depender de uno que ya haya corrido.
    settings = Settings()
    active_provider, active_model = _active_selection(settings)
    diagnostics = asyncio.run(ProviderRegistry(settings).diagnostics())
    checks = [
        _DoctorCheck("python", "pass", sys.version.split()[0]),
        _tool_check("git", ["git", "--version"], required=True),
        _tool_check("node", ["node", "--version"], required=True),
        _tool_check("npm", ["npm.cmd", "--version"], required=True),
        _tool_check(
            "ollama_client",
            ["ollama", "--version"],
            required=active_provider == "ollama",
        ),
        *_provider_checks(diagnostics, active_provider, active_model),
        _workspace_path_check(settings.workspace_root),
        _writability_check(
            "workspace_write",
            settings.workspace_root,
            "Agentarium necesita crear/escribir en AGENTARIUM_WORKSPACE_ROOT.",
        ),
        _writability_check(
            "temp_write",
            Path(tempfile.gettempdir()),
            "Sin permiso de escritura en la carpeta temporal del sistema -- "
            "ver docs/guides/windows-setup.md si esto corta test.ps1 con "
            "errores de permiso en pytest.",
        ),
        _DoctorCheck("database", "pass", settings.resolved_database_url()),
        _DoctorCheck("model_concurrency", "pass", str(settings.model_concurrency)),
        _DoctorCheck("repo_root", "pass", str(project_root())),
    ]
    markers = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}
    for check in checks:
        line = f"[{markers[check.status]}] {check.name}: {check.detail}"
        if check.hint:
            line += f"\n       -> {check.hint}"
        typer.echo(line)
    failed = sum(1 for check in checks if check.status == "fail")
    warned = sum(1 for check in checks if check.status == "warn")
    typer.echo(
        f"\n{len(checks) - failed - warned} ok, {warned} advertencia(s), {failed} error(es)."
    )
    if failed:
        raise typer.Exit(_EXIT_DOCTOR_FAILED)


@app.command("init")
def initialize() -> None:
    """Inicializa directorios y base de datos."""
    service = build_application()
    try:
        recovered = service.initialize()
    except (MigrationFailedError, SchemaTooNewError, BackupValidationError) as exc:
        _exit_on_startup_failure(exc)
    typer.echo(f"Agentarium inicializado. Tareas recuperadas: {recovered}")


@db_app.command("restore")
def restore_database(
    backup: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Backup a restaurar (VACUUM INTO)."),
    ],
) -> None:
    """Restaura la base de datos activa desde un backup validado.

    No sobreescribe una base viva a ciegas: exige el mismo lock que usa el
    arranque normal, valida el backup con PRAGMA integrity_check antes de
    tocar nada, y conserva la base reemplazada como `<archivo>.failed-<ts>`
    en vez de borrarla.
    """
    settings = get_settings()
    db_path = sqlite_path_from_url(settings.resolved_database_url())
    if db_path is None:
        typer.echo(
            "El backend configurado no es un archivo SQLite "
            f"({settings.resolved_database_url()!r}); no hay nada que restaurar.",
            err=True,
        )
        raise typer.Exit(_EXIT_RESTORE_FAILED)
    try:
        restore_database_file(db_path, backup)
    except RestoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(_EXIT_RESTORE_FAILED) from exc
    typer.echo(f"Base de datos restaurada desde {backup}.")


@app.command()
def start() -> None:
    """Inicia la API en primer plano."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "agentarium.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


@app.command("test")
def run_tests() -> None:
    """Ejecuta las pruebas Python."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=project_root(),
        check=False,
        shell=False,
    )
    raise typer.Exit(completed.returncode)


@project_app.command("create")
def create_project(
    goal: Annotated[str, typer.Argument(help="Objetivo del proyecto.")],
    title: Annotated[str | None, typer.Option("--title", "-t", help="Título opcional.")] = None,
) -> None:
    service = _service()
    project = service.create_project(goal, title)
    typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False))


@project_app.command("import")
def import_project(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Carpeta del proyecto existente a importar.",
        ),
    ],
    goal: Annotated[str, typer.Argument(help="Objetivo del proyecto.")],
    title: Annotated[str | None, typer.Option("--title", "-t", help="Título opcional.")] = None,
) -> None:
    """Importa un proyecto existente sin modificar el original."""
    service = _service()
    try:
        project = asyncio.run(service.import_project(str(source), goal, title))
    except ImportSourceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(10) from exc
    typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False))


@project_app.command("export")
def export_project(
    project_id: Annotated[str, typer.Argument(help="ID del proyecto.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", resolve_path=True, help="Carpeta donde escribir el patch/bundle."
        ),
    ] = None,
    formats: Annotated[
        list[str] | None,
        typer.Option("--format", "-f", help="patch y/o bundle; repetible. Por defecto, ambos."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Mostrar qué se exportaría, sin escribir nada."),
    ] = False,
) -> None:
    """Exporta el historial propio del proyecto como patch y/o bundle."""
    resolved_formats = frozenset(formats) if formats else frozenset({"patch", "bundle"})
    invalid = resolved_formats - {"patch", "bundle"}
    if invalid:
        raise typer.BadParameter(f"Formato desconocido: {sorted(invalid)}")
    service = _service()
    try:
        if dry_run:
            result = asyncio.run(service.export_preview(project_id))
        else:
            if output is None:
                raise typer.BadParameter("--output es obligatorio salvo con --dry-run")
            result = asyncio.run(
                service.export_project(project_id, str(output), formats=resolved_formats)
            )
    except ExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(11) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@project_app.command("run")
def run_project(
    project_id: Annotated[str, typer.Argument(help="ID del proyecto.")],
) -> None:
    service = _service()
    project = asyncio.run(service.run_project(project_id))
    typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False))


@project_app.command("pause")
def pause_project(project_id: str) -> None:
    project = _service().pause_project(project_id)
    typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False))


@project_app.command("resume")
def resume_project(project_id: str) -> None:
    project = _service().resume_project(project_id)
    typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False))


@project_app.command("status")
def project_status(project_id: str) -> None:
    typer.echo(json.dumps(_project_detail(project_id), indent=2, ensure_ascii=False))


@project_app.command("report")
def project_report(project_id: str) -> None:
    """Informe de entrega: qué se pidió, qué cambió, qué se verificó, qué quedó sin verificar."""
    typer.echo(json.dumps(_delivery_report(project_id), indent=2, ensure_ascii=False))


@benchmark_app.command("run")
def benchmark_run(
    cases: Annotated[
        list[str] | None,
        typer.Option("--case", "-c", help="Id del caso; repetible. Por defecto, todos."),
    ] = None,
    models: Annotated[
        list[str] | None,
        typer.Option(
            "--model",
            "-m",
            help="Objetivo 'proveedor:modelo'; repetible. Por defecto, mock.",
        ),
    ] = None,
    repetitions: Annotated[
        int, typer.Option("--repetitions", "-r", min=1, max=10)
    ] = 1,
    suite: Annotated[str, typer.Option("--suite", help="Nombre del ledger.")] = "default",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Sólo listar lo que falta, sin ejecutar."),
    ] = False,
    rerun: Annotated[
        bool,
        typer.Option("--rerun", help="Repetir combinaciones ya registradas."),
    ] = False,
) -> None:
    """Ejecuta la matriz de casos. Reanudable: omite lo ya registrado."""
    from agentarium.benchmarks import (
        BenchmarkLedger,
        BenchmarkRunner,
        DirtyCheckout,
        InvalidBenchmarkCase,
        MissingModelDigest,
        ModelTarget,
        SuiteDrift,
        load_cases,
        plan_matrix,
    )

    try:
        selected = load_cases(only=list(cases) if cases else None)
    except InvalidBenchmarkCase as exc:
        typer.echo(f"Caso inválido: {exc}", err=True)
        raise typer.Exit(2) from exc

    try:
        targets = [ModelTarget.parse(raw) for raw in (models or ["mock"])]
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    planned = plan_matrix(selected, targets, repetitions)

    service = _service()
    runner = BenchmarkRunner(service, BenchmarkLedger(_ledger_path(suite)))
    try:
        asyncio.run(runner.freeze_identity())
    except DirtyCheckout as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from exc
    identity = runner.identity
    try:
        runner.assert_digests_available(planned)
        pending = runner.pending(planned, rerun=rerun)
    except MissingModelDigest as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(5) from exc
    except SuiteDrift as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc

    typer.echo(
        json.dumps(
            {
                "suite": suite,
                "identity": identity.model_dump(mode="json"),
                "model_digests": {
                    target.label: runner.digest_for(target) for target in targets
                },
                "planned": len(planned),
                "pending": len(pending),
                "skipped": len(planned) - len(pending),
                "runs": [
                    {
                        "case_id": run.case.id,
                        "target": run.target.label,
                        "repetition": run.repetition,
                    }
                    for run in pending
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    if dry_run or not pending:
        return

    def _echo(record: Any) -> None:
        typer.echo(
            f"  {record.case_id} · {record.provider}:{record.model} · "
            f"#{record.repetition} → {record.category.value} "
            f"({record.duration_seconds}s)"
        )

    try:
        asyncio.run(runner.execute(planned, rerun=rerun, on_progress=_echo))
    except SuiteDrift as exc:
        # Drift can also surface mid-matrix, when the runtime is revalidated
        # before a run. Same exit code as the preflight, not a traceback.
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc
    except MissingModelDigest as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(5) from exc


@benchmark_app.command("report")
def benchmark_report(
    suite: Annotated[str, typer.Option("--suite")] = "default",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Directorio donde escribir el informe."),
    ] = None,
) -> None:
    """Genera el informe desde el ledger, sin volver a ejecutar nada."""
    from agentarium.benchmarks import (
        BenchmarkLedger,
        build_payload,
        dump_json_report,
        render_markdown,
    )

    ledger = BenchmarkLedger(_ledger_path(suite))
    payload = build_payload(list(ledger.latest_by_key().values()))
    markdown = render_markdown(payload)
    destination = output or _ledger_path(suite).parent
    dump_json_report(destination / "report.json", payload)
    (destination / "report.md").write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    typer.echo(f"Informe escrito en {destination}")


_SUITE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _ledger_path(suite: str) -> Path:
    # Rejected, never sanitized: silently stripping characters would make
    # `a/b` and `ab` share one ledger and mix two baselines.
    if not _SUITE_PATTERN.match(suite):
        raise typer.BadParameter(
            f"Nombre de suite inválido: {suite!r}. Usá letras, dígitos, "
            "punto, guion o guion bajo (hasta 64 caracteres)."
        )
    return project_root() / "runtime" / "benchmarks" / suite / "ledger.jsonl"


def _project_detail(project_id: str) -> dict[str, Any]:
    """Prefer the running API (single writer, WAL-safe reads) over opening
    the SQLite file from a second process; fall back when no server answers."""
    settings = get_settings()
    url = f"http://{settings.api_host}:{settings.api_port}/api/projects/{project_id}"
    try:
        response = httpx.get(url, timeout=1.0)
        response.raise_for_status()
        return dict(response.json())
    except httpx.HTTPError:
        return _service().project_detail(project_id)


def _repair_center() -> list[dict[str, Any]]:
    """Same API-preferring/SQLite-fallback shape as `_project_detail`."""
    settings = get_settings()
    url = f"http://{settings.api_host}:{settings.api_port}/api/repair-center"
    try:
        response = httpx.get(url, timeout=1.0)
        response.raise_for_status()
        return list(response.json())
    except httpx.HTTPError:
        return _service().repair_center()


def _delivery_report(project_id: str) -> dict[str, Any]:
    """Same API-preferring/SQLite-fallback shape as `_project_detail`."""
    settings = get_settings()
    url = f"http://{settings.api_host}:{settings.api_port}/api/projects/{project_id}/report"
    try:
        response = httpx.get(url, timeout=1.0)
        response.raise_for_status()
        return dict(response.json())
    except httpx.HTTPError:
        return _service().delivery_report(project_id)


@repair_app.command("list")
def repair_list() -> None:
    """Lista tareas que necesitan reparación en todos los proyectos."""
    typer.echo(json.dumps(_repair_center(), indent=2, ensure_ascii=False))


@repair_app.command("retry")
def repair_retry(
    work_item_id: Annotated[str, typer.Argument(help="ID del work item.")],
) -> None:
    """Reintenta un work item fallido, agotado o con cambios solicitados."""
    item = _service().retry_work_item(work_item_id)
    typer.echo(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))


@repair_app.command("recover")
def repair_recover(
    work_item_id: Annotated[str, typer.Argument(help="ID del work item.")],
    artifact_id: Annotated[str, typer.Argument(help="ID del artefacto a recuperar.")],
) -> None:
    """Reevalúa un artefacto ya producido como si fuera un candidato nuevo."""
    item = asyncio.run(_service().recover_artifact(work_item_id, artifact_id))
    typer.echo(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))


@repair_app.command("rework")
def repair_rework(
    work_item_id: Annotated[str, typer.Argument(help="ID del work item completado.")],
    reason: Annotated[str, typer.Argument(help="Motivo de la revisión.")],
    acceptance_criteria: Annotated[
        list[str] | None,
        typer.Option(
            "--acceptance-criteria", "-a", help="Criterio adicional; repetible."
        ),
    ] = None,
) -> None:
    """Abre una revisión nueva encadenada a un work item ya completado."""
    item = _service().rework_work_item(work_item_id, reason, acceptance_criteria)
    typer.echo(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))


@repair_app.command("escalate")
def repair_escalate(
    work_item_id: Annotated[str, typer.Argument(help="ID del work item.")],
    reason: Annotated[str, typer.Argument(help="Motivo de la escalación.")],
) -> None:
    """Escala un work item a una decisión humana."""
    approval = _service().escalate_work_item(work_item_id, reason)
    typer.echo(json.dumps(approval.model_dump(mode="json"), indent=2, ensure_ascii=False))


@repair_app.command("candidate")
def repair_candidate(
    work_item_id: Annotated[str, typer.Argument(help="ID del work item.")],
    payload_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Archivo JSON con la forma de SubmitCandidateRequest.",
        ),
    ],
) -> None:
    """Envía un candidato armado por un operador humano (título, resumen y
    archivos) como si fuera un artefacto producido por un agente."""
    from pydantic import ValidationError

    from agentarium.api.schemas import SubmitCandidateRequest

    try:
        raw = json.loads(payload_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{payload_file} no es JSON válido: {exc}") from exc
    try:
        body = SubmitCandidateRequest.model_validate(raw)
    except ValidationError as exc:
        raise typer.BadParameter(f"{payload_file} no tiene la forma esperada: {exc}") from exc

    item = asyncio.run(
        _service().submit_candidate(
            work_item_id,
            title=body.title,
            summary=body.summary,
            files=[file.model_dump(mode="json") for file in body.files],
        )
    )
    typer.echo(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))


def _version(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except OSError:
        return None
    return (result.stdout or result.stderr).strip() or None


if __name__ == "__main__":
    app()
