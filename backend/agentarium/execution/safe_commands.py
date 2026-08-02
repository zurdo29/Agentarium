from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


class CommandRejected(PermissionError):
    pass


@lru_cache(maxsize=256)
def _denied_token_expression(token: str) -> re.Pattern[str]:
    """How a denied token is recognised inside a command line.

    Plain substring matching turned ordinary filenames into denials:
    `models.py` contains `del`, `registry.py` contains `reg`, `arm_utils.py`
    contains `rm`. A token made only of word characters must therefore match a
    whole word — `del` still denies `del`, `cmd /c del x` and `C:\\bin\\del`,
    but no longer `models.py`. Tokens that already carry separators (e.g.
    `Invoke-Expression`) keep matching literally, since word boundaries would
    not help there.
    """
    if token.isalnum():
        # `_` counts as part of a word, so `format_helper.py` is a filename and
        # not a `format` invocation. `.` `/` `\` `-` and whitespace stay
        # boundaries, so `del.exe`, `C:\bin\del` and `rm -rf` remain denied.
        return re.compile(rf"(?<![0-9A-Za-z_]){re.escape(token)}(?![0-9A-Za-z_])")
    return re.compile(re.escape(token))


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    cwd: str
    stdout: str
    stderr: str
    return_code: int
    timed_out: bool


class SafeCommandExecutor:
    def __init__(self, workspace_root: Path, policy_path: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        self.allowed: set[str] = {
            str(executable).casefold() for executable in policy["commands"]["allow"]
        }
        self.denied_tokens: set[str] = {
            token.casefold() for token in policy["commands"]["deny_tokens"]
        }
        self.default_timeout = int(policy["commands"]["timeout_seconds"])
        self.max_log_bytes = int(policy["commands"]["max_log_bytes"])
        self.environment_allow = {
            str(key).casefold() for key in policy["environment_allow"]
        }

    async def execute(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        if not command:
            raise CommandRejected("Command cannot be empty")
        executable = Path(command[0]).name.casefold()
        if executable not in self.allowed:
            raise CommandRejected(f"Executable is not allowed: {executable}")
        flattened = " ".join(command).casefold()
        if any(
            _denied_token_expression(token).search(flattened)
            for token in self.denied_tokens
        ):
            raise CommandRejected("Command contains a denied token")
        resolved_cwd = await asyncio.to_thread(cwd.resolve)
        if resolved_cwd != self.workspace_root and self.workspace_root not in resolved_cwd.parents:
            raise CommandRejected("Working directory must be inside the configured workspace")

        environment: dict[str, str] = {}
        included: set[str] = set()
        for key, value in os.environ.items():
            normalized = key.casefold()
            if (
                normalized not in self.environment_allow
                or normalized in included
                or "secret" in normalized
            ):
                continue
            canonical = "PATH" if normalized == "path" else key
            environment[canonical] = value
            included.add(normalized)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=resolved_cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds or self.default_timeout,
            )
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        return CommandResult(
            command=command,
            cwd=str(resolved_cwd),
            stdout=self._truncate(stdout_bytes),
            stderr=self._truncate(stderr_bytes),
            return_code=process.returncode if process.returncode is not None else -1,
            timed_out=timed_out,
        )

    def _truncate(self, value: bytes) -> str:
        return value[: self.max_log_bytes].decode("utf-8", errors="replace")
