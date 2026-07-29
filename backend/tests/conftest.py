from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from agentarium.config.settings import Settings, project_root
from agentarium.repositories import Database
from agentarium.services import ApplicationService, build_application


@pytest.fixture()
def service(tmp_path: Path) -> Iterator[ApplicationService]:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'agentarium.db').as_posix()}",
        workspace_root=tmp_path / "workspaces",
        config_root=project_root() / "configs",
        provider_state_path=tmp_path / "provider-selection.json",
        model_concurrency=1,
    )
    database = Database(settings.resolved_database_url())
    application = build_application(settings, database)
    application.initialize()
    yield application
    database.dispose()
