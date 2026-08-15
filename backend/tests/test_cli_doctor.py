from __future__ import annotations

import socket
from pathlib import Path

import pytest
from agentarium.cli import (
    _EXIT_DOCTOR_FAILED,
    _NODE_MIN_VERSION,
    _active_selection,
    _node_check,
    _provider_checks,
    _tool_check,
    _workspace_path_check,
    _writability_check,
)
from agentarium.config.settings import Settings
from agentarium.llm import ProviderDiagnostic, ProviderSelection, ProviderSelectionStore
from typer.testing import CliRunner

# `doctor` (cli.py) is P4.5's diagnostic entry point -- these tests cover
# the pure logic directly (no network, no subprocess) plus a couple of
# CliRunner wiring tests. `doctor` deliberately builds `Settings()`
# directly rather than `get_settings()` (which calls `ensure_directories()`
# -- a side effect `doctor` must survive being unable to perform), so
# these tests don't need the `get_settings.cache_clear()` dance that other
# CLI tests use.


def _closed_port() -> int:
    """A real, currently-unused local port: bind then immediately close,
    so a connection attempt fails fast (connection refused) instead of
    timing out against an address nothing is listening on yet vs. never."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- _active_selection --------------------------------------------------


def test_active_selection_falls_back_to_env_when_nothing_saved(tmp_path: Path) -> None:
    settings = Settings(
        provider_state_path=tmp_path / "provider-selection.json",
        provider="ollama",
        model="qwen2.5-coder:7b",
    )
    assert _active_selection(settings) == ("ollama", "qwen2.5-coder:7b")


def test_active_selection_prefers_saved_selection_over_env(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-selection.json"
    ProviderSelectionStore(state_path).save(
        ProviderSelection(provider="openai_compatible", model="saved-model")
    )
    settings = Settings(
        provider_state_path=state_path, provider="ollama", model="env-model"
    )
    assert _active_selection(settings) == ("openai_compatible", "saved-model")


def test_active_selection_mock_with_no_model_is_fine(tmp_path: Path) -> None:
    settings = Settings(
        provider_state_path=tmp_path / "provider-selection.json",
        provider="mock",
        model="",
    )
    assert _active_selection(settings) == ("mock", None)


# -- _tool_check ----------------------------------------------------------


def test_tool_check_missing_and_not_required_is_pass() -> None:
    check = _tool_check(
        "ollama_client", ["definitely-not-a-real-command-xyz"], required=False
    )
    assert check.status == "pass"


def test_tool_check_missing_and_required_is_fail() -> None:
    check = _tool_check("git", ["definitely-not-a-real-command-xyz"], required=True)
    assert check.status == "fail"
    assert check.hint is not None


# -- _node_check ------------------------------------------------------------
# Pure function taking an already-fetched version string -- no subprocess,
# no real alternate Node install needed to exercise the version gate.


def test_node_check_missing_is_fail() -> None:
    check = _node_check(None)
    assert check.status == "fail"


def test_node_check_just_below_minimum_is_fail() -> None:
    # _NODE_MIN_VERSION is (22, 13, 0) today; this stays correct even if
    # that constant changes later.
    major, minor, _patch = _NODE_MIN_VERSION
    below = f"v{major}.{minor - 1}.0"
    check = _node_check(below)
    assert check.status == "fail"
    assert check.hint is not None


def test_node_check_at_minimum_is_pass() -> None:
    major, minor, patch = _NODE_MIN_VERSION
    check = _node_check(f"v{major}.{minor}.{patch}")
    assert check.status == "pass"


def test_node_check_22_12_0_is_fail_and_22_13_0_is_pass() -> None:
    # The exact boundary the user asked to pin down explicitly.
    assert _node_check("v22.12.0").status == "fail"
    assert _node_check("v22.13.0").status == "pass"


def test_node_check_well_above_minimum_is_pass() -> None:
    assert _node_check("v24.18.0").status == "pass"


# -- _provider_checks -------------------------------------------------------


def _diagnostic(**overrides: object) -> ProviderDiagnostic:
    defaults: dict[str, object] = {
        "name": "ollama",
        "label": "Ollama local",
        "endpoint": "http://127.0.0.1:11434",
        "reachable": True,
        "ready": True,
        "models": ["qwen2.5-coder:7b"],
        "message": "1 modelo disponible.",
    }
    defaults.update(overrides)
    return ProviderDiagnostic(**defaults)  # type: ignore[arg-type]


def test_provider_checks_active_ready_model_pulled_is_pass() -> None:
    checks = _provider_checks([_diagnostic()], "ollama", "qwen2.5-coder:7b")
    ollama_check = next(check for check in checks if check.name == "ollama")
    assert ollama_check.status == "pass"


def test_provider_checks_active_model_not_pulled_names_the_model_for_ollama() -> None:
    checks = _provider_checks([_diagnostic()], "ollama", "llama3:not-pulled")
    ollama_check = next(check for check in checks if check.name == "ollama")

    assert ollama_check.status == "fail"
    # The detail line itself must name the missing model, not just a
    # generic "not ready" message that doesn't say which model is wrong.
    assert "llama3:not-pulled" in ollama_check.detail
    # And the already-pulled model the diagnostic *does* have should show
    # up too, so the user can see what's actually available.
    assert "qwen2.5-coder:7b" in ollama_check.detail
    assert ollama_check.hint is not None
    assert "ollama pull llama3:not-pulled" in ollama_check.hint


def test_provider_checks_active_model_not_pulled_names_the_model_for_openai_compatible() -> None:
    diagnostic = _diagnostic(
        name="openai_compatible",
        label="Servidor compatible con OpenAI",
        endpoint="http://127.0.0.1:1234/v1",
        models=["local-model-a"],
    )
    checks = _provider_checks([diagnostic], "openai_compatible", "not-on-server")
    check = next(c for c in checks if c.name == "openai_compatible")

    assert check.status == "fail"
    assert "not-on-server" in check.detail
    assert check.hint is not None
    # Ollama-specific "pull" advice must never leak into the
    # openai_compatible hint -- there's no local pull step for it.
    assert "ollama pull" not in check.hint
    assert "not-on-server" in check.hint


def test_provider_checks_active_unreachable_is_fail() -> None:
    checks = _provider_checks(
        [_diagnostic(reachable=False, ready=False, models=[])],
        "ollama",
        "qwen2.5-coder:7b",
    )
    ollama_check = next(check for check in checks if check.name == "ollama")
    assert ollama_check.status == "fail"


def test_provider_checks_inactive_unreachable_is_warn_not_fail() -> None:
    # provider is "mock" -- ollama being unreachable must never fail doctor
    # just because the user isn't using it.
    checks = _provider_checks(
        [_diagnostic(reachable=False, ready=False, models=[])], "mock", None
    )
    ollama_check = next(check for check in checks if check.name == "ollama")
    assert ollama_check.status == "warn"


def test_provider_checks_provider_config_fails_without_model_for_non_mock() -> None:
    checks = _provider_checks([_diagnostic()], "ollama", None)
    config_check = next(check for check in checks if check.name == "provider_config")
    assert config_check.status == "fail"
    assert config_check.hint is not None


def test_provider_checks_provider_config_passes_for_mock_without_model() -> None:
    checks = _provider_checks([], "mock", None)
    config_check = next(check for check in checks if check.name == "provider_config")
    assert config_check.status == "pass"


# -- _workspace_path_check ---------------------------------------------------


def test_workspace_path_check_short_path_is_pass(tmp_path: Path) -> None:
    check = _workspace_path_check(tmp_path)
    assert check.status == "pass"


def test_workspace_path_check_long_path_is_warn() -> None:
    long_root = Path("C:\\" + ("x" * 120))
    check = _workspace_path_check(long_root)
    assert check.status == "warn"
    assert check.hint is not None
    assert "AGENTARIUM_WORKSPACE_ROOT" in check.hint


# -- _writability_check -------------------------------------------------------


def test_writability_check_creates_missing_directory_and_passes(tmp_path: Path) -> None:
    # The regression P4.5 explicitly asked for: a workspace that was never
    # created yet (typical on a fresh install), but whose parent is
    # writable, must be a real `pass` -- doctor creates it, not fail on it.
    target = tmp_path / "not-created-yet" / "workspace"
    assert not target.exists()

    check = _writability_check("workspace_write", target, "hint")

    assert check.status == "pass"
    assert target.is_dir()


def test_writability_check_fails_when_target_is_unwritable(tmp_path: Path) -> None:
    # Portable way to force a real OSError without touching Windows ACLs:
    # a path that tries to use a plain *file* as a directory component.
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "child"

    check = _writability_check("workspace_write", target, "hint")

    assert check.status == "fail"
    assert check.hint == "hint"


# -- CliRunner: wiring only, per the established test_import_cli_* rationale -


def _doctor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = (tmp_path / "db.sqlite").as_posix()
    monkeypatch.setenv("AGENTARIUM_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("AGENTARIUM_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv(
        "AGENTARIUM_PROVIDER_STATE_PATH", str(tmp_path / "provider-selection.json")
    )
    # Closed local ports: both probes fail fast (connection refused) instead
    # of depending on a real Ollama/openai-compatible server being up, or
    # timing out against an address nothing answers on.
    monkeypatch.setenv("AGENTARIUM_OLLAMA_URL", f"http://127.0.0.1:{_closed_port()}")
    monkeypatch.setenv(
        "AGENTARIUM_OPENAI_COMPATIBLE_URL", f"http://127.0.0.1:{_closed_port()}"
    )


def test_doctor_cli_mock_provider_has_no_fail_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _doctor_env(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENTARIUM_PROVIDER", "mock")

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "[FAIL]" not in result.output


def test_doctor_cli_provider_without_model_exits_with_doctor_failed_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentarium.cli import app

    _doctor_env(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENTARIUM_PROVIDER", "ollama")
    monkeypatch.delenv("AGENTARIUM_MODEL", raising=False)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == _EXIT_DOCTOR_FAILED, result.output
    assert "AGENTARIUM_MODEL" in result.output
