from agentarium.agents.roles import RoleCatalog
from agentarium.config.settings import project_root
from agentarium.domain.enums import AgentRole


def test_local_worker_uses_one_long_generation_window() -> None:
    catalog = RoleCatalog(project_root() / "configs" / "roles" / "default.yaml")

    worker = catalog.get(AgentRole.IMPLEMENTATION_WORKER)

    assert worker.timeout_seconds == 300
    assert worker.max_retries == 0
