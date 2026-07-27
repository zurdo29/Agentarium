import json
from pathlib import Path

from agentarium.artifacts import ArtifactStore


def test_artifact_is_materialized_and_verified(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "workspaces")
    path, checksum = store.materialize("project", "task", 1, {"value": 42})

    assert store.verify(path, checksum)
    saved = tmp_path / path
    assert json.loads(saved.read_text(encoding="utf-8")) == {"value": 42}

    saved.write_text('{"value": 41}\n', encoding="utf-8")
    assert not store.verify(path, checksum)
