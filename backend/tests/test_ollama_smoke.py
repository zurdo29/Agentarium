import httpx
import pytest
from agentarium.config.settings import get_settings


def _ollama_available() -> bool:
    try:
        return httpx.get(f"{get_settings().ollama_url.rstrip('/')}/api/tags", timeout=1).is_success
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _ollama_available(), reason="Ollama is not running")
def test_ollama_server_smoke() -> None:
    response = httpx.get(f"{get_settings().ollama_url.rstrip('/')}/api/tags", timeout=2)
    assert response.is_success
    assert "models" in response.json()
