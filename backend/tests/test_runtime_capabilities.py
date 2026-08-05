from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.execution.capabilities import (
    RuntimeCapabilityManifest,
    build_runtime_capabilities,
)
from pydantic import ValidationError


def _policy_path() -> Path:
    return project_root() / "configs" / "policies" / "security.yaml"


def test_the_real_security_policy_declares_a_stdlib_only_deny_network_manifest() -> None:
    manifest = build_runtime_capabilities(_policy_path())

    # Regression pin: we never accidentally ship an allowed third-party
    # package or an open network policy in the real project config.
    assert manifest.third_party_packages_allowed == ()
    assert manifest.network_policy == "deny"
    assert manifest.executables_allowed
    assert manifest.executables_allowed == tuple(sorted(manifest.executables_allowed))
    # Allowed by policy is not the same claim as available on this machine —
    # enforced by the model itself, this is also a construction sanity check.
    assert set(manifest.executables_available) <= set(manifest.executables_allowed)


def test_python_version_matches_the_live_interpreter() -> None:
    manifest = build_runtime_capabilities(_policy_path())

    assert manifest.python_version == sys.version.split()[0]


def test_missing_packages_and_network_keys_default_to_stdlib_only_deny(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "security.yaml"
    policy_path.write_text(
        "commands:\n  allow: [git, python]\n",
        encoding="utf-8",
    )

    manifest = build_runtime_capabilities(policy_path)

    assert manifest.third_party_packages_allowed == ()
    assert manifest.network_policy == "deny"


def test_declared_third_party_packages_round_trip_sorted(tmp_path: Path) -> None:
    policy_path = tmp_path / "security.yaml"
    policy_path.write_text(
        "commands:\n  allow: [git, python]\n"
        "packages:\n  allowed: [pandas, numpy]\n",
        encoding="utf-8",
    )

    manifest = build_runtime_capabilities(policy_path)

    assert manifest.third_party_packages_allowed == ("numpy", "pandas")


def test_available_is_the_subset_that_resolves_on_this_machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Allowed-by-policy and actually-installed are different claims: a
    # machine without Node.js still lists `node` as allowed, never as
    # available. Deterministic via a stubbed shutil.which, independent of
    # what's really on this machine's PATH.
    policy_path = tmp_path / "security.yaml"
    policy_path.write_text(
        "commands:\n  allow: [git, python, madeupthingthatdoesnotexist]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agentarium.execution.capabilities.shutil.which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )

    manifest = build_runtime_capabilities(policy_path)

    assert manifest.executables_allowed == (
        "git",
        "madeupthingthatdoesnotexist",
        "python",
    )
    assert manifest.executables_available == ("git",)


def test_an_invalid_network_policy_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeCapabilityManifest(
            python_version="3.14.0",
            executables_allowed=["git"],
            executables_available=["git"],
            network_policy="allow",  # type: ignore[arg-type]
        )


def test_collections_are_tuples_not_mutable_lists() -> None:
    # frozen=True alone only blocks reassigning an attribute, not mutating a
    # list a field happens to hold. Every collection field must be a tuple
    # so there is no mutable container to reach into at all.
    manifest = build_runtime_capabilities(_policy_path())

    assert isinstance(manifest.executables_allowed, tuple)
    assert isinstance(manifest.executables_available, tuple)
    assert isinstance(manifest.third_party_packages_allowed, tuple)


def test_available_outside_allowed_is_rejected() -> None:
    with pytest.raises(ValidationError, match="subset"):
        RuntimeCapabilityManifest(
            python_version="3.14.0",
            executables_allowed=["git"],
            executables_available=["git", "node"],
        )
