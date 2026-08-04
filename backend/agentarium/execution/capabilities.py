"""What the runtime can actually do, declared as data (P2.1, ADR 0028).

ADR 0020 added a prompt sentence telling the worker its execution sandbox is
stdlib-only, no network, no installs — verified live to change nothing: the
model imported Flask again in the very next retry. This is the next rung:
the same facts as structured data in `SOLICITUD.payload`, not only prose.

`executables_allowed`/`executables_available` and `network_policy` describe
what Agentarium's own pipeline does with a delivery (which interpreter runs
your `.py`, whether it will ever reach the network) — not a sandbox applied
to a delivered script's own `subprocess`/`socket` calls, which does not
exist at any level today. This manifest declares a policy; it does not by
itself enforce one. See ADR 0028 for the gap this leaves open on purpose.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RuntimeCapabilityManifest(BaseModel):
    """A snapshot taken once at startup, never mutated. Not a domain entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    python_version: str = Field(min_length=1)
    # Allowed by policy (security.yaml commands.allow) — may include names
    # that are not actually installed on this machine.
    executables_allowed: list[str]
    # The subset of executables_allowed that resolves on PATH right now.
    executables_available: list[str]
    # Third-party only: empty means stdlib-only. Never the packages that
    # merely happen to be importable in Agentarium's own venv (ADR 0020).
    third_party_packages_allowed: list[str] = Field(default_factory=list)
    # A policy, not a technical guarantee: nothing here adds OS-level
    # network isolation (see benchmarks/functional.py's own docstring for
    # the same caveat on the one path that already strips site-packages).
    network_policy: Literal["deny"] = "deny"


def build_runtime_capabilities(policy_path: Path) -> RuntimeCapabilityManifest:
    # Independent read of the same file SafeCommandExecutor and
    # WorkspaceMaterializer already parse on their own — the established
    # idiom for this policy file, not a new pattern.
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    allowed = sorted(str(executable) for executable in policy["commands"]["allow"])
    available = [name for name in allowed if shutil.which(name) is not None]
    packages = policy.get("packages", {})
    return RuntimeCapabilityManifest(
        python_version=sys.version.split()[0],
        executables_allowed=allowed,
        executables_available=available,
        third_party_packages_allowed=sorted(
            str(package) for package in packages.get("allowed", [])
        ),
        network_policy=policy.get("network", "deny"),
    )
