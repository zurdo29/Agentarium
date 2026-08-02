"""The denied-token gate must deny commands, not filenames that contain them.

Substring matching rejected `models.py` (`del`), `registry.py` (`reg`) and
`arm_utils.py` (`rm`). `models.py` is exactly what the library benchmark case
produces, so the gate was blocking measurement — and, before that, real work.
These tests pin both directions: still denied, no longer over-denied.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.execution.safe_commands import CommandRejected, SafeCommandExecutor


def _executor(root: Path) -> SafeCommandExecutor:
    root.mkdir(parents=True, exist_ok=True)
    return SafeCommandExecutor(
        root,
        project_root() / "configs" / "policies" / "security.yaml",
    )


@pytest.mark.parametrize(
    "command",
    [
        ["python", "models.py"],
        ["python", "registry.py"],
        ["python", "arm_utils.py"],
        ["python", "format_helper.py"],
        ["python", "delivery/report.py"],
        ["python", "C:/tmp/test_one_model_0/main.py"],
    ],
)
@pytest.mark.asyncio
async def test_ordinary_filenames_that_embed_a_token_are_not_denied(
    tmp_path: Path,
    command: list[str],
) -> None:
    executor = _executor(tmp_path / "workspaces")
    # Reaching the working-directory check means the token gate let it through.
    with pytest.raises(CommandRejected, match="Working directory"):
        await executor.execute(command, cwd=tmp_path / "afuera")


@pytest.mark.parametrize(
    "command",
    [
        ["python", "-c", "del algo"],
        ["python", "-c", "rm -rf ."],
        ["python", "C:/bin/del"],
        ["python", "-c", "format c:"],
        ["python", "-c", "reg add HKLM"],
        ["python", "-c", "Invoke-Expression $x"],
        ["python", "-c", "Start-Process cmd"],
    ],
)
@pytest.mark.asyncio
async def test_a_real_denied_token_is_still_denied(
    tmp_path: Path,
    command: list[str],
) -> None:
    executor = _executor(tmp_path / "workspaces")
    with pytest.raises(CommandRejected, match="denied token"):
        await executor.execute(command, cwd=tmp_path / "workspaces")


@pytest.mark.asyncio
async def test_an_executable_outside_the_allowlist_is_still_rejected(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path / "workspaces")
    with pytest.raises(CommandRejected, match="not allowed"):
        await executor.execute(["curl", "https://example.com"], cwd=tmp_path / "workspaces")
