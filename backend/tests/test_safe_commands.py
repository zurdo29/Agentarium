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


@pytest.mark.asyncio
async def test_safe_executor_rejects_unlisted_command(tmp_path: Path) -> None:
    executor = SafeCommandExecutor(
        tmp_path,
        project_root() / "configs" / "policies" / "security.yaml",
    )
    with pytest.raises(CommandRejected):
        await executor.execute(["powershell.exe", "-Command", "Get-Date"], cwd=tmp_path)
