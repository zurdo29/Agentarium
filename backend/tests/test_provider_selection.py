from __future__ import annotations

from pathlib import Path

from agentarium.config.settings import Settings, project_root
from agentarium.llm import ProviderSelection, ProviderSelectionStore
from agentarium.services import build_application


def test_provider_selection_survives_application_rebuild(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-selection.json"
    ProviderSelectionStore(state_path).save(
        ProviderSelection(provider="ollama", model="qwen2.5-coder:7b")
    )
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}",
        workspace_root=tmp_path / "workspaces",
        config_root=project_root() / "configs",
        provider_state_path=state_path,
    )

    application = build_application(settings)

    assert application.roles.active_provider() == "ollama"
    assert application.roles.active_model() == "qwen2.5-coder:7b"
    application.database.dispose()


def test_invalid_provider_state_falls_back_to_configured_default(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-selection.json"
    state_path.write_text('{"provider":"ollama","model":null}', encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}",
        workspace_root=tmp_path / "workspaces",
        config_root=project_root() / "configs",
        provider_state_path=state_path,
    )

    application = build_application(settings)

    assert application.roles.active_provider() == "mock"
    application.database.dispose()
