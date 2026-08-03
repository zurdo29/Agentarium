from __future__ import annotations

import asyncio
import sys
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agentarium.domain.models import ScriptExecutionContract

from .safe_commands import CommandRejected, CommandResult, SafeCommandExecutor
from .workspace import WorkspaceFileEvidence

VALIDATION_CONTRACT_VERSION = "profiles-v6"


class ValidationProfile(StrEnum):
    WORKSPACE_INVENTORY = "workspace_inventory"
    PYTHON_SYNTAX = "python_syntax"
    JSON_SYNTAX = "json_syntax"
    JAVASCRIPT_SYNTAX = "javascript_syntax"
    WEB_APPLICATION = "web_application"
    SCRIPT_EXECUTION = "script_execution"


@dataclass(frozen=True)
class ValidationProfileResult:
    profile: ValidationProfile
    targets: tuple[str, ...]
    result: CommandResult

    @property
    def passed(self) -> bool:
        return self.result.return_code == 0 and not self.result.timed_out

    def as_evidence(self) -> dict[str, object]:
        return {
            "check": "validation_profile",
            "profile": self.profile.value,
            "targets": list(self.targets),
            "command": self.result.command,
            "cwd": self.result.cwd,
            "stdout": self.result.stdout,
            "stderr": self.result.stderr,
            "return_code": self.result.return_code,
            "timed_out": self.result.timed_out,
            "passed": self.passed,
            "contract_version": VALIDATION_CONTRACT_VERSION,
        }


class ValidationProfileExecutor:
    """Maps verified workspace files to application-owned, shell-free commands."""

    INVENTORY_CODE = (
        "from pathlib import Path;"
        "paths=sorted(p.as_posix() for p in Path('.').rglob('*') "
        "if p.is_file() and '.git' not in p.parts);"
        "print('\\n'.join(paths))"
    )
    PYTHON_SYNTAX_CODE = (
        "from pathlib import Path;"
        "import sys;"
        "path=sys.argv[1];"
        "compile(Path(path).read_text(encoding='utf-8'),path,'exec')"
    )

    def __init__(self, workspace_root: Path, policy_path: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.executor = SafeCommandExecutor(self.workspace_root, policy_path)

    async def validate(
        self,
        project_id: str,
        files: list[WorkspaceFileEvidence],
        *,
        validation_root: Path | None = None,
        acceptance_criteria: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        execution_contract: ScriptExecutionContract | None = None,
    ) -> list[ValidationProfileResult]:
        project_scope = await asyncio.to_thread(
            (self.workspace_root / project_id).resolve
        )
        project_root = (
            await asyncio.to_thread(validation_root.resolve)
            if validation_root is not None
            else await asyncio.to_thread((project_scope / "project").resolve)
        )
        if not self._is_within(project_root, project_scope):
            raise CommandRejected("Project validation directory escaped the workspace")

        targets = [self._relative_target(project_root, evidence) for evidence in files]
        commands: list[tuple[ValidationProfile, tuple[str, ...], list[str]]] = [
            (
                ValidationProfile.WORKSPACE_INVENTORY,
                tuple(path.as_posix() for path in targets),
                [sys.executable, "-c", self.INVENTORY_CODE],
            )
        ]
        for target in targets:
            relative = target.as_posix()
            suffix = target.suffix.casefold()
            if suffix == ".py":
                commands.append(
                    (
                        ValidationProfile.PYTHON_SYNTAX,
                        (relative,),
                        [sys.executable, "-c", self.PYTHON_SYNTAX_CODE, relative],
                    )
                )
            elif suffix == ".json":
                commands.append(
                    (
                        ValidationProfile.JSON_SYNTAX,
                        (relative,),
                        [sys.executable, "-m", "json.tool", relative],
                    )
                )
            elif suffix in {".js", ".mjs", ".cjs"}:
                commands.append(
                    (
                        ValidationProfile.JAVASCRIPT_SYNTAX,
                        (relative,),
                        ["node", "--check", relative],
                    )
                )

        javascript_targets = [
            target
            for target in targets
            if target.suffix.casefold() in {".js", ".mjs", ".cjs"}
        ]
        web_flags = self._web_contract_flags(acceptance_criteria or [])
        html_targets = [
            target
            for target in targets
            if target.suffix.casefold() in {".html", ".htm"}
        ]
        for script_target in javascript_targets:
            for index_name in ("index.html", "index.htm"):
                sibling = script_target.parent / index_name
                if (
                    sibling not in html_targets
                    and (project_root / sibling).is_file()
                ):
                    html_targets.append(sibling)
        root_index = Path("index.html")
        if (
            web_flags
            and not html_targets
            and (project_root / root_index).is_file()
        ):
            html_targets.append(root_index)
        for html_target in html_targets:
            relevant_scripts = [
                script_target
                for script_target in javascript_targets
                if script_target.parent == html_target.parent
            ]
            command = [
                sys.executable,
                "-m",
                "agentarium.execution.web_smoke",
                "--html",
                html_target.as_posix(),
            ]
            for script_target in relevant_scripts:
                command.extend(["--script", script_target.as_posix()])
            command.extend(web_flags)
            commands.append(
                (
                    ValidationProfile.WEB_APPLICATION,
                    (
                        html_target.as_posix(),
                        *(target.as_posix() for target in relevant_scripts),
                    ),
                    command,
                )
            )

        results = [
            await self._execute(profile, command_targets, command, project_root)
            for profile, command_targets, command in commands
        ]

        script_execution_requested = (
            self._script_execution_requested(
                [*(acceptance_criteria or []), *(expected_outputs or [])]
            )
            or execution_contract is not None
        )
        script_targets = [
            target for target in targets if target.suffix.casefold() == ".py"
        ]
        if script_execution_requested and execution_contract is not None:
            results.extend(
                await self._run_declared_contract(
                    execution_contract, script_targets, project_root
                )
            )
        elif script_execution_requested and not script_targets:
            results.append(
                ValidationProfileResult(
                    profile=ValidationProfile.SCRIPT_EXECUTION,
                    targets=(),
                    result=CommandResult(
                        command=[],
                        cwd=str(project_root),
                        stdout="",
                        stderr=(
                            "Los criterios reclaman una herramienta ejecutable "
                            "(Python, script, linea de comandos) pero la entrega "
                            "no incluye ningun archivo fuente .py."
                        ),
                        return_code=1,
                        timed_out=False,
                    ),
                )
            )
        elif script_execution_requested:
            for script_target in script_targets:
                results.append(
                    await self._execute(
                        ValidationProfile.SCRIPT_EXECUTION,
                        (script_target.as_posix(),),
                        [sys.executable, script_target.name],
                        project_root / script_target.parent,
                    )
                )

        return results

    async def _run_declared_contract(
        self,
        contract: ScriptExecutionContract,
        script_targets: list[Path],
        project_root: Path,
    ) -> list[ValidationProfileResult]:
        """Invoke the entrypoint a work item declared, with its real args,
        instead of running every `.py` file blind (ADR 0027). A declared
        contract is the sole authority once present: only its own entrypoint
        gets executed. Every other delivered `.py` file already gets
        `PYTHON_SYNTAX` (unconditional, checked earlier) but is no longer
        also run blind as `SCRIPT_EXECUTION` — a helper module perfectly
        valid when imported can be invalid to run standalone, so doing that
        would reject good auxiliary code, not validate it."""
        matched_target = next(
            (
                target
                for target in script_targets
                if target.as_posix().casefold() == contract.entrypoint.casefold()
            ),
            None,
        )
        if matched_target is None:
            # Covers both "no .py files at all" and "has .py files, but not
            # this one" — a declared contract that names a file the delivery
            # doesn't have is always a hard failure, never a silent fall
            # back to blind mode.
            return [
                ValidationProfileResult(
                    profile=ValidationProfile.SCRIPT_EXECUTION,
                    targets=(),
                    result=CommandResult(
                        command=[],
                        cwd=str(project_root),
                        stdout="",
                        stderr=(
                            "El contrato de ejecucion declara el entrypoint "
                            f"'{contract.entrypoint}' pero la entrega no lo "
                            "incluye."
                        ),
                        return_code=1,
                        timed_out=False,
                    ),
                )
            ]

        contract_cwd = project_root / matched_target.parent
        if contract.produces:
            # The delivery must not be credited for an artifact that was
            # already lying around — materialized alongside the script, or
            # left over from a previous attempt in the same worktree. Same
            # discipline as `benchmarks/functional.py::_prepare_run`
            # ("a delivery that ships the answer must not be credited for
            # it"). Erasing it first means the check below only passes if
            # this run actually (re)created it.
            (contract_cwd / contract.produces).unlink(missing_ok=True)
        results = [
            await self._execute(
                ValidationProfile.SCRIPT_EXECUTION,
                (matched_target.as_posix(),),
                [sys.executable, matched_target.name, *contract.args],
                contract_cwd,
            )
        ]
        contract_result = results[0]
        if (
            contract_result.passed
            and contract.produces
            and not (contract_cwd / contract.produces).is_file()
        ):
            results.append(
                ValidationProfileResult(
                    profile=ValidationProfile.SCRIPT_EXECUTION,
                    targets=(matched_target.as_posix(),),
                    result=CommandResult(
                        command=contract_result.result.command,
                        cwd=str(contract_cwd),
                        stdout="",
                        stderr=(
                            "El contrato de ejecucion declara que se produce "
                            f"'{contract.produces}' pero no aparece tras "
                            "ejecutar el entrypoint."
                        ),
                        return_code=1,
                        timed_out=False,
                    ),
                )
            )
        return results

    @staticmethod
    def _script_execution_requested(criteria: list[str]) -> bool:
        combined = " ".join(criteria)
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", combined)
            if not unicodedata.combining(character)
        ).casefold()
        return any(
            token in normalized
            for token in (
                "python",
                "script",
                "linea de comandos",
                "command line",
                "terminal",
                "consola",
            )
        )

    @staticmethod
    def _web_contract_flags(criteria: list[str]) -> list[str]:
        combined = " ".join(criteria)
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", combined)
            if not unicodedata.combining(character)
        ).casefold()
        flags: list[str] = []
        game_context = any(
            token in normalized
            for token in (
                "juego",
                "game",
                "jugador",
                "player",
                "enemig",
                "enemy",
                "pac-man",
                "pacman",
                "laberinto",
                "maze",
                "partida",
                "puntuacion",
                "score",
                "coleccionable",
                "pellet",
                "fantasma",
            )
        )
        movement_requested = any(
            token in normalized
            for token in ("naveg", "move", "mover", "movimiento", "desplaz")
        )
        keyboard_accessibility = any(
            token in normalized
            for token in (
                "accesib",
                "teclado",
                "keyboard",
                "tabulacion",
                "tab navigation",
            )
        )
        if game_context and movement_requested:
            flags.append("--navigation")
        if keyboard_accessibility and not game_context:
            flags.append("--keyboard-accessibility")
        create_requested = any(
            token in normalized
            for token in ("crear", "agregar", "anadir", "create", "add")
        )
        edit_requested = any(
            token in normalized
            for token in ("editar", "actualiz", "modific", "edit", "update")
        )
        delete_requested = any(
            token in normalized
            for token in ("eliminar", "borrar", "delete", "remove")
        )
        if create_requested and edit_requested and delete_requested:
            flags.append("--crud")
        if any(token in normalized for token in ("buscar", "busqueda", "search")):
            flags.append("--search")
        if any(token in normalized for token in ("filtrar", "filtro", "filter")):
            flags.append("--filtering")
        if delete_requested and any(
            token in normalized
            for token in ("confirm", "confirmacion")
        ):
            flags.append("--destructive-guard")
        if any(
            token in normalized
            for token in ("estado vacio", "empty state", "sin registros", "sin datos")
        ):
            flags.append("--empty-state")
        if "localstorage" in normalized:
            flags.append("--local-storage")
        if "json" in normalized and any(
            token in normalized
            for token in ("export", "import")
        ):
            flags.append("--json-transfer")
        if any(
            token in normalized
            for token in (
                "guia",
                "instrucciones",
                "documentacion",
                "readme",
                "como ejecutar",
            )
        ):
            flags.append("--guide")
        if game_context and any(
            token in normalized
            for token in ("evit", "agresiv", "enemy", "enemig", "persigu")
        ):
            flags.append("--pursuit")
        if "pac-man" in normalized or "pacman" in normalized:
            flags.append("--pacman")
        if game_context and any(
            token in normalized
            for token in ("pared", "muro", "wall", "laberinto", "maze")
        ) and any(
            token in normalized
            for token in ("bloq", "colision", "collision", "block")
        ):
            flags.append("--wall-collision")
        if game_context and ("vida" in normalized or "lives" in normalized):
            flags.append("--lives")
        if game_context and any(
            token in normalized
            for token in (
                "victoria",
                "ganar",
                "win",
                "reinici",
                "restart",
                "reset",
            )
        ):
            flags.append("--win-restart")
        return flags

    async def _execute(
        self,
        profile: ValidationProfile,
        targets: tuple[str, ...],
        command: list[str],
        cwd: Path,
    ) -> ValidationProfileResult:
        try:
            result = await self.executor.execute(
                command,
                cwd=cwd,
                timeout_seconds=20,
            )
        except (CommandRejected, OSError) as exc:
            result = CommandResult(
                command=command,
                cwd=str(cwd),
                stdout="",
                stderr=str(exc),
                return_code=-1,
                timed_out=False,
            )
        return ValidationProfileResult(profile=profile, targets=targets, result=result)

    def _relative_target(
        self,
        project_root: Path,
        evidence: WorkspaceFileEvidence,
    ) -> Path:
        target = (self.workspace_root.parent / evidence.path).resolve()
        if not self._is_within(target, project_root):
            raise CommandRejected("Validation target escaped the project workspace")
        return target.relative_to(project_root)

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents
