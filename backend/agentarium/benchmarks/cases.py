"""Loading the versioned cases from disk."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from agentarium.config.settings import project_root

from .contracts import BenchmarkCase


class InvalidBenchmarkCase(ValueError):
    pass


def cases_root() -> Path:
    return project_root() / "benchmarks" / "cases"


def load_case(path: Path) -> BenchmarkCase:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidBenchmarkCase(f"{path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidBenchmarkCase(f"{path.name}: el caso debe ser un mapeo YAML")
    try:
        case = BenchmarkCase.model_validate(raw)
    except ValidationError as exc:
        raise InvalidBenchmarkCase(f"{path.name}: {exc}") from exc
    if case.id != path.stem:
        raise InvalidBenchmarkCase(
            f"{path.name}: el id {case.id!r} no coincide con el nombre del archivo"
        )
    return case


def load_cases(root: Path | None = None, *, only: list[str] | None = None) -> list[BenchmarkCase]:
    directory = root or cases_root()
    if not directory.is_dir():
        raise InvalidBenchmarkCase(f"No existe el directorio de casos: {directory}")
    cases = [load_case(path) for path in sorted(directory.glob("*.yaml"))]
    if not cases:
        raise InvalidBenchmarkCase(f"No hay casos versionados en {directory}")
    if only:
        wanted = set(only)
        unknown = wanted - {case.id for case in cases}
        if unknown:
            raise InvalidBenchmarkCase(f"Casos desconocidos: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.id in wanted]
    return cases
