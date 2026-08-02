"""What the suite was actually measuring, captured per run.

`prompt_versions` and `case_schema_version` freeze what *we* declare. They say
nothing about the code that ran, the weights behind a model name, or the
machine underneath. A tag like `qwen3:4b` is mutable — `ollama pull` can
replace it without the name changing — so two runs of "the same model" can be
two different models, and a baseline that mixes them is not a baseline.

Everything here is recorded per run and compared on resume.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentarium.config.settings import Settings, project_root


class RuntimeIdentity(BaseModel):
    """The environment-wide half of a run's identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agentarium_commit: str = Field(min_length=1)
    # A dirty tree means the commit does not identify what ran, so a dirty run
    # is never comparable with a clean one — the flag is part of the identity,
    # not a footnote.
    agentarium_dirty: bool
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    concurrency: int = Field(ge=1)
    ollama_version: str | None = None

    def differences(self, other: RuntimeIdentity) -> list[str]:
        changes: list[str] = []
        for field in type(self).model_fields:
            mine = getattr(self, field)
            theirs = getattr(other, field)
            if mine != theirs:
                changes.append(f"{field}: {mine} → {theirs}")
        return changes


def git_commit(root: Path | None = None) -> tuple[str, bool]:
    """`(commit, dirty)` for the working tree that is about to be measured."""
    cwd = root or project_root()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        # Not a checkout (a release copy, a tarball). Recorded as such rather
        # than guessed, and it will simply never compare equal to a checkout.
        return "unknown", True
    return commit, bool(status)


async def ollama_version(settings: Settings) -> str | None:
    endpoint = settings.ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.provider_probe_timeout_seconds) as client:
            response = await client.get(f"{endpoint}/api/version")
            response.raise_for_status()
            return str(response.json().get("version") or "").strip() or None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def ollama_model_digests(settings: Settings) -> dict[str, str]:
    """Tag → digest, so a re-pulled tag stops looking like the same model."""
    endpoint = settings.ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.provider_probe_timeout_seconds) as client:
            response = await client.get(f"{endpoint}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return {}
    digests: dict[str, str] = {}
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("model") or item.get("name") or "").strip()
        digest = str(item.get("digest") or "").strip()
        if name and digest:
            digests[name] = digest
    return digests


async def capture_identity(settings: Settings) -> RuntimeIdentity:
    commit, dirty = git_commit()
    return RuntimeIdentity(
        agentarium_commit=commit,
        agentarium_dirty=dirty,
        python_version=sys.version.split()[0],
        # Deliberately coarse: a patch-level OS update should not invalidate a
        # baseline, a different machine or architecture should.
        platform=f"{platform.system()}-{platform.machine()}",
        concurrency=settings.model_concurrency,
        ollama_version=await ollama_version(settings),
    )
