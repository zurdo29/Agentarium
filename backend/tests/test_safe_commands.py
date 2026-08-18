from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.execution.safe_commands import CommandRejected, SafeCommandExecutor


@pytest.mark.asyncio
async def test_safe_executor_captures_real_evidence(tmp_path: Path) -> None:
    executor = SafeCommandExecutor(
        tmp_path,
        project_root() / "configs" / "policies" / "security.yaml",
    )
    result = await executor.execute(["python", "--version"], cwd=tmp_path)
    assert result.return_code == 0
    assert "Python" in result.stdout or "Python" in result.stderr
    assert not result.timed_out
    assert result.started


@pytest.mark.asyncio
async def test_safe_executor_rejects_unlisted_command(tmp_path: Path) -> None:
    executor = SafeCommandExecutor(
        tmp_path,
        project_root() / "configs" / "policies" / "security.yaml",
    )
    with pytest.raises(CommandRejected):
        await executor.execute(["powershell.exe", "-Command", "Get-Date"], cwd=tmp_path)


@pytest.mark.asyncio
async def test_safe_executor_marks_started_true_even_on_timeout(tmp_path: Path) -> None:
    """Gate-MVP.2 (ADR 0041): a process that really launched and then got
    killed for running too long is still real evidence -- started tracks
    whether a process launched, never whether it finished in time."""
    executor = SafeCommandExecutor(
        tmp_path,
        project_root() / "configs" / "policies" / "security.yaml",
    )
    result = await executor.execute(
        ["python", "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert result.timed_out
    assert result.started
