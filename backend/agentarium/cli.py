from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from typing import Annotated

import httpx
import typer

from agentarium.config.settings import get_settings, project_root
from agentarium.services import ApplicationService, build_application

app = typer.Typer(
    name="agentarium",
    help="Empresa local de agentes con artefactos verificables.",
    no_args_is_help=True,
)
project_app = typer.Typer(help="Crear y controlar proyectos.", no_args_is_help=True)
app.add_typer(project_app, name="project")


def _service() -> ApplicationService:
    service = build_application()
    service.initialize()
    return service


@app.command()
def doctor() -> None:
    """Comprueba herramientas, almacenamiento y proveedor local."""
    settings = get_settings()
    results = {
        "python": sys.version.split()[0],
        "node": _version(["node", "--version"]),
        "npm": _version(["npm.cmd", "--version"]),
        "git": _version(["git", "--version"]),
        "ollama_client": _version(["ollama", "--version"]),
        "ollama_server": _ollama_status(settings.ollama_url),
        "workspace": str(project_root()),
        "database": settings.resolved_database_url(),
        "model_concurrency": settings.model_concurrency,
    }
    typer.echo(json.dumps(results, indent=2, ensure_ascii=False))


@app.command("init")
def initialize() -> None:
    """Inicializa directorios y base de datos."""
    service = _service()
    typer.echo(
        f"Agentarium inicializado. Tareas recuperadas: {service.repository.recover_interrupted()}"
    )


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
    detail = _service().project_detail(project_id)
    typer.echo(json.dumps(detail, indent=2, ensure_ascii=False))


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


def _ollama_status(base_url: str) -> str:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=2)
        return "available" if response.is_success else f"http_{response.status_code}"
    except httpx.HTTPError:
        return "unavailable"


if __name__ == "__main__":
    app()
