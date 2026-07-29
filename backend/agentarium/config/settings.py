from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTARIUM_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "sqlite:///runtime/agentarium.db"
    workspace_root: Path = Field(default_factory=lambda: project_root() / "workspaces")
    config_root: Path = Field(default_factory=lambda: project_root() / "configs")
    provider_state_path: Path = Field(
        default_factory=lambda: project_root() / "runtime" / "provider-selection.json"
    )
    model_concurrency: int = Field(default=1, ge=1, le=8)
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    frontend_origin: str = "http://127.0.0.1:3000"
    provider: Literal["mock", "ollama", "openai_compatible"] = "mock"
    model: str = ""
    provider_probe_timeout_seconds: float = Field(default=1.5, ge=0.1, le=10)
    ollama_url: str = "http://127.0.0.1:11434"
    openai_compatible_url: str = "http://127.0.0.1:1234/v1"
    openai_api_key: str = ""

    def resolved_database_url(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return self.database_url
        path = Path(self.database_url.removeprefix(prefix))
        if not path.is_absolute():
            path = project_root() / path
        return f"{prefix}{path.as_posix()}"

    def ensure_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.provider_state_path.parent.mkdir(parents=True, exist_ok=True)
        url = self.resolved_database_url()
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
