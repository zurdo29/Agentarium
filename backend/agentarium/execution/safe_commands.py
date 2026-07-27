from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class CommandRejected(PermissionError):
    pass


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
        self.allowed: set[str] = set(policy["commands"]["allow"])
        self.denied_tokens: set[str] = {
            token.casefold() for token in policy["commands"]["deny_tokens"]
        }
        self.default_timeout = int(policy["commands"]["timeout_seconds"])
        self.max_log_bytes = int(policy["commands"]["max_log_bytes"])
        self.environment_allow = set(policy["environment_allow"])

    async def execute(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        if not command:
            raise CommandRejected("Command cannot be empty")
        executable = Path(command[0]).name
        if executable not in self.allowed:
            raise CommandRejected(f"Executable is not allowed: {executable}")
        flattened = " ".join(command).casefold()
        if any(token in flattened for token in self.denied_tokens):
            raise CommandRejected("Command contains a denied token")
        resolved_cwd = await asyncio.to_thread(cwd.resolve)
        if resolved_cwd != self.workspace_root and self.workspace_root not in resolved_cwd.parents:
            raise CommandRejected("Working directory must be inside the configured workspace")

        environment = {
            key: value
            for key, value in os.environ.items()
            if key in self.environment_allow and "secret" not in key.casefold()
        }
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=resolved_cwd,
            env=environment,
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
