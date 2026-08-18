from __future__ import annotations

import ast
import asyncio
import sys
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agentarium.domain.models import ScriptExecutionContract

from .capabilities import RuntimeCapabilityManifest
from .safe_commands import CommandRejected, CommandResult, SafeCommandExecutor
from .workspace import WorkspaceFileEvidence

# P2.2 (ADR 0029): bumped when IMPORT_PREFLIGHT was activated, following the
# ADR 0016 precedent — activating a new validation profile bumps this
# constant so retries already in flight don't inherit a failure produced by
# semantics that didn't exist when they started.
# Gate-MVP.2 (ADR 0041): v7 -> v8 when PYTHON_UNDEFINED_NAMES was activated,
# same precedent.
VALIDATION_CONTRACT_VERSION = "profiles-v8"


class ValidationProfile(StrEnum):
    WORKSPACE_INVENTORY = "workspace_inventory"
    PYTHON_SYNTAX = "python_syntax"
    IMPORT_PREFLIGHT = "import_preflight"
    PYTHON_UNDEFINED_NAMES = "python_undefined_names"
    JSON_SYNTAX = "json_syntax"
    JAVASCRIPT_SYNTAX = "javascript_syntax"
    WEB_APPLICATION = "web_application"
    SCRIPT_EXECUTION = "script_execution"


# P3.4 (ADR 0034): profiles that never execute the delivered content's own
# logic -- WORKSPACE_INVENTORY lists files via a fixed snippet,
# PYTHON_SYNTAX only `compile()`s (never `exec()`s), JSON_SYNTAX only
# parses, JAVASCRIPT_SYNTAX only runs `node --check`, IMPORT_PREFLIGHT is a
# static in-process AST walk with no subprocess at all, WEB_APPLICATION
# (web_smoke.py) parses HTML and regex-matches the JS source text without
# ever interpreting it, and PYTHON_UNDEFINED_NAMES (Gate-MVP.2, ADR 0041)
# runs `ruff check --select F821` -- static name-resolution analysis, never
# `exec()`s the delivered file. Declared explicitly and checked fail-closed
# in `_execute()`: a profile not on this list is treated as code execution
# and blocked whenever the caller disallows it, including any profile added
# here later without also being added to this set.
NON_EXECUTING_PROFILES = frozenset(
    {
        ValidationProfile.WORKSPACE_INVENTORY,
        ValidationProfile.PYTHON_SYNTAX,
        ValidationProfile.IMPORT_PREFLIGHT,
        ValidationProfile.PYTHON_UNDEFINED_NAMES,
        ValidationProfile.JSON_SYNTAX,
        ValidationProfile.JAVASCRIPT_SYNTAX,
        ValidationProfile.WEB_APPLICATION,
    }
)


@dataclass(frozen=True)
class ValidationProfileResult:
    profile: ValidationProfile
    targets: tuple[str, ...]
    result: CommandResult
    # P3.4 (ADR 0034): True only for the synthetic result `_execute()`
    # returns instead of running an execution-requiring profile against an
    # imported project -- structural marker consumers key off directly
    # (`check.get("blocked_by_authority")`), never text-matched from stderr.
    blocked_by_authority: bool = False

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
            "blocked_by_authority": self.blocked_by_authority,
            "started": self.result.started,
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

    def __init__(
        self,
        workspace_root: Path,
        policy_path: Path,
        *,
        capabilities: RuntimeCapabilityManifest | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.executor = SafeCommandExecutor(self.workspace_root, policy_path)
        self.capabilities = capabilities

    async def validate(
        self,
        project_id: str,
        files: list[WorkspaceFileEvidence],
        *,
        validation_root: Path | None = None,
        acceptance_criteria: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        execution_contract: ScriptExecutionContract | None = None,
        allow_project_code_execution: bool = True,
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
                commands.append(
                    (
                        ValidationProfile.PYTHON_UNDEFINED_NAMES,
                        (relative,),
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "check",
                            "--isolated",
                            "--no-cache",
                            "--select",
                            "F821",
                            relative,
                        ],
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
            await self._execute(
                profile,
                command_targets,
                command,
                project_root,
                allow_project_code_execution=allow_project_code_execution,
            )
            for profile, command_targets, command in commands
        ]

        script_targets = [
            target for target in targets if target.suffix.casefold() == ".py"
        ]
        import_preflight_result: ValidationProfileResult | None = None
        if script_targets:
            third_party_allowed = (
                frozenset(self.capabilities.third_party_packages_allowed)
                if self.capabilities is not None
                else frozenset()
            )
            import_preflight_result = self._check_import_preflight(
                script_targets, project_root, third_party_allowed
            )
            results.append(import_preflight_result)

        script_execution_requested = (
            self._script_execution_requested(
                [*(acceptance_criteria or []), *(expected_outputs or [])]
            )
            or execution_contract is not None
        )
        if script_execution_requested and not allow_project_code_execution:
            # P3.4 (ADR 0034): checked before the IMPORT_PREFLIGHT-skip branch
            # below on purpose. Authority is a permanent property of the
            # project, not a candidate-fixable defect -- it must produce a
            # blocked_by_authority result even when this same candidate also
            # failed IMPORT_PREFLIGHT, so a caller downstream can never treat
            # this as a retryable import problem instead of a project-level
            # block (see Orchestrator._evaluate_candidate, which checks
            # authority_blocked before import_preflight_failure for the same
            # reason).
            results.append(
                self._authority_blocked_result(
                    ValidationProfile.SCRIPT_EXECUTION, (), project_root
                )
            )
        elif import_preflight_result is not None and not import_preflight_result.passed:
            # La capacidad ya se sabe no soportada: correr SCRIPT_EXECUTION
            # de verdad sólo repetiría el mismo fallo unos milisegundos más
            # tarde, vía un ModuleNotFoundError real en vez de uno anticipado.
            pass
        elif script_execution_requested and execution_contract is not None:
            results.extend(
                await self._run_declared_contract(
                    execution_contract,
                    script_targets,
                    project_root,
                    allow_project_code_execution=allow_project_code_execution,
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
                        started=False,
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
                        allow_project_code_execution=allow_project_code_execution,
                    )
                )

        return results

    async def _run_declared_contract(
        self,
        contract: ScriptExecutionContract,
        script_targets: list[Path],
        project_root: Path,
        *,
        allow_project_code_execution: bool,
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
                        started=False,
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
                allow_project_code_execution=allow_project_code_execution,
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
                        started=False,
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

    @staticmethod
    def _check_import_preflight(
        python_targets: list[Path],
        project_root: Path,
        third_party_allowed: frozenset[str],
    ) -> ValidationProfileResult:
        """Static, in-process capability check (P2.2, ADR 0029) — no
        subprocess. Walks the *entire* AST (`ast.walk`, not just top-level
        statements): an import behind a function body or a
        `try/except ImportError` fallback guard is still an import, and a
        policy that only catches the top-level case would repeat the exact
        failure ADR 0020 already documented (prose-only guidance changes
        nothing) — just hidden behind a guard instead of ignored outright.
        """
        rejected: dict[str, list[str]] = {}
        for target in python_targets:
            try:
                source = (project_root / target).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=target.as_posix())
            except (OSError, UnicodeDecodeError, SyntaxError):
                # PYTHON_SYNTAX already owns reporting an unreadable or
                # syntactically invalid file; this profile must not
                # duplicate that, and must never let the exception escape
                # and take the whole validate() call down with it.
                continue
            target_dir = project_root / target.parent
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level > 0 or node.module is None:
                        continue  # relative import: part of this same delivery
                    roots = [node.module.split(".", 1)[0]]
                else:
                    continue
                for root in roots:
                    if (
                        root in sys.stdlib_module_names
                        or root in third_party_allowed
                        or ValidationProfileExecutor._resolves_as_sibling(
                            target_dir, project_root, root
                        )
                    ):
                        continue
                    rejected.setdefault(root, [])
                    posix_target = target.as_posix()
                    if posix_target not in rejected[root]:
                        rejected[root].append(posix_target)

        if not rejected:
            return ValidationProfileResult(
                profile=ValidationProfile.IMPORT_PREFLIGHT,
                targets=(),
                result=CommandResult(
                    command=[],
                    cwd=str(project_root),
                    stdout="",
                    stderr="",
                    return_code=0,
                    timed_out=False,
                    started=False,
                ),
            )

        lines = [
            "Import no permitido: los siguientes modulos no forman parte de "
            "la biblioteca estandar ni de "
            "runtime_capabilities.third_party_packages_allowed. Elegi una "
            "alternativa de la biblioteca estandar o declaralo como "
            "limitacion conocida de la tarea.",
        ]
        for module in sorted(rejected):
            files = ", ".join(sorted(rejected[module]))
            lines.append(f"- {module} (usado en {files})")

        return ValidationProfileResult(
            profile=ValidationProfile.IMPORT_PREFLIGHT,
            targets=tuple(sorted(rejected)),
            result=CommandResult(
                command=[],
                cwd=str(project_root),
                stdout="",
                stderr="\n".join(lines),
                return_code=1,
                timed_out=False,
                started=False,
            ),
        )

    @staticmethod
    def _resolves_as_sibling(directory: Path, project_root: Path, root: str) -> bool:
        """Whether `root` is importable as a local module or package
        reachable from `directory` — location-aware on purpose, not a flat
        "does this name exist anywhere in the delivery" bag (that
        earlier version had two failure modes: it rejected
        `from library import models` when `library/` sat next to the
        importing file, because only file *stems* were collected, never
        directory/package names; and it silently allowed `import flask`
        whenever *any* unrelated file elsewhere in the tree happened to be
        named `flask.py`, even though that file is not on the path Python
        would actually search from here — exactly the late
        `ModuleNotFoundError` this profile exists to catch early).

        Walks up from `directory` through every ancestor up to (and never
        past) `project_root` — checking `directory` alone missed a very
        common layout: a module referring to its own containing package by
        top-level name (`library/api.py` doing `from library import
        models`) is not a sibling of `api.py`'s own directory (`library/`)
        — `library` sits one level *up*, at `project_root`. Once Python
        resolves an absolute import it always searches from `sys.path`,
        fixed once at process start by whichever file was actually invoked
        as the entrypoint, never from the directory of whichever file's
        source happens to contain the `import` statement — a submodule
        several directories deep can import a package sitting anywhere
        between its own directory and wherever the process was launched
        from. This profile cannot know in advance which file will be the
        entrypoint, so every level from the importing file up to
        `project_root` counts as a plausible answer — erring toward
        not-false-positiving, same principle as the exemption below,
        extended one dimension. Never climbs past `project_root`: an
        unrelated `otro/flask.py` sitting outside the importing file's own
        ancestor chain must still not excuse `import flask` in
        `app/main.py` — that is the second, distinct bug this method
        already fixes, and widening the search must not reopen it.
        """
        current = directory
        while True:
            if (current / f"{root}.py").is_file():
                return True
            package_dir = current / root
            if package_dir.is_dir() and any(package_dir.rglob("*.py")):
                return True
            if current == project_root:
                return False
            current = current.parent

    def _authority_blocked_result(
        self,
        profile: ValidationProfile,
        targets: tuple[str, ...],
        cwd: Path,
    ) -> ValidationProfileResult:
        """P3.4 (ADR 0034): the synthetic result `_execute()` returns instead
        of running an execution-requiring profile when the caller disallows
        project code execution (an imported project, until real process
        isolation exists) -- same synthetic-`CommandResult` shape already
        used for IMPORT_PREFLIGHT/CommandRejected, marked structurally via
        `blocked_by_authority` rather than through the stderr text."""
        return ValidationProfileResult(
            profile=profile,
            targets=targets,
            result=CommandResult(
                command=[],
                cwd=str(cwd),
                stdout="",
                stderr=(
                    "Ejecucion deshabilitada: el proyecto esta marcado como "
                    "importado y todavia no existe una frontera de "
                    "aislamiento real del proceso (P3.4)."
                ),
                return_code=1,
                timed_out=False,
                started=False,
            ),
            blocked_by_authority=True,
        )

    async def _execute(
        self,
        profile: ValidationProfile,
        targets: tuple[str, ...],
        command: list[str],
        cwd: Path,
        *,
        allow_project_code_execution: bool,
    ) -> ValidationProfileResult:
        if profile not in NON_EXECUTING_PROFILES and not allow_project_code_execution:
            return self._authority_blocked_result(profile, targets, cwd)
        try:
            result = await self.executor.execute(
                command,
                cwd=cwd,
                timeout_seconds=20,
            )
        except (CommandRejected, OSError) as exc:
            # Rejected by policy before launch, or the OS itself never
            # produced a process (FileNotFoundError and friends) -- either
            # way nothing started, so this is never "executed" (ADR 0041).
            result = CommandResult(
                command=command,
                cwd=str(cwd),
                stdout="",
                stderr=str(exc),
                return_code=-1,
                timed_out=False,
                started=False,
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
