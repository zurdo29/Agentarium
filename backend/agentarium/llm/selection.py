from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator


class ProviderSelection(BaseModel):
    provider: Literal["mock", "ollama", "openai_compatible"]
    model: str | None = None

    @model_validator(mode="after")
    def require_model_for_inference(self) -> ProviderSelection:
        if self.provider != "mock" and not (self.model or "").strip():
            raise ValueError("A real provider selection requires a model")
        if self.model is not None:
            self.model = self.model.strip() or None
        return self


class ProviderSelectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProviderSelection | None:
        if not self.path.exists():
            return None
        try:
            return ProviderSelection.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            return None

    def save(self, selection: ProviderSelection) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            selection.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
