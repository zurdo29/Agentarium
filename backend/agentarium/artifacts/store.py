from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def materialize(
        self,
        project_id: str,
        work_item_id: str,
        attempt: int,
        content: dict[str, Any],
    ) -> tuple[str, str]:
        directory = self.workspace_root / project_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{work_item_id}-attempt-{attempt}.json"
        serialized = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(serialized + "\n", encoding="utf-8")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        return str(path.relative_to(self.workspace_root.parent)), checksum

    def verify(self, relative_path: str, checksum: str) -> bool:
        path = (self.workspace_root.parent / relative_path).resolve()
        if self.workspace_root != path and self.workspace_root not in path.parents:
            return False
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum
