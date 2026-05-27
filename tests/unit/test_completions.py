"""Pin ``complete_workspace_name``'s broad-catch + opt-in stderr-diagnostic contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped_core import ConfigError
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError
from untaped_workspace.infrastructure import WorkspaceRegistryRepository


@pytest.fixture
def silent_completion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_COMPLETION_DEBUG", raising=False)


def _stub_entries(
    monkeypatch: pytest.MonkeyPatch,
    behaviour: Exception | list[Workspace],
) -> None:
    if isinstance(behaviour, Exception):

        def _raise(self: WorkspaceRegistryRepository) -> list[Workspace]:
            raise behaviour

        monkeypatch.setattr(WorkspaceRegistryRepository, "entries", _raise)
    else:
        rows = list(behaviour)

        def _return(self: WorkspaceRegistryRepository) -> list[Workspace]:
            return rows

        monkeypatch.setattr(WorkspaceRegistryRepository, "entries", _return)


def test_happy_path_filters_by_prefix(
    monkeypatch: pytest.MonkeyPatch,
    silent_completion_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_entries(
        monkeypatch,
        [
            Workspace(name="alpha", path=Path("/tmp/alpha")),
            Workspace(name="alphabet", path=Path("/tmp/alphabet")),
            Workspace(name="beta", path=Path("/tmp/beta")),
        ],
    )
    assert list(complete_workspace_name("alph")) == ["alpha", "alphabet"]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "exc",
    [
        RegistryError("invalid workspace registry entry"),
        ConfigError("could not parse /tmp/config.yml: …"),
        OSError(13, "Permission denied"),
    ],
    ids=["RegistryError", "ConfigError", "OSError"],
)
def test_any_error_silent_by_default(
    monkeypatch: pytest.MonkeyPatch,
    silent_completion_env: None,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
) -> None:
    # Completion must never raise. The catch covers `Exception` so a
    # `ConfigError` from broken YAML, a malformed registry entry, or an
    # `OSError` from a permission glitch on `~/.untaped/config.yml` all
    # produce an empty completion list rather than a traceback the shell
    # would swallow silently.
    _stub_entries(monkeypatch, exc)
    assert list(complete_workspace_name("a")) == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (RegistryError("invalid workspace registry entry"), "invalid workspace registry entry"),
        (ConfigError("could not parse /tmp/config.yml: x"), "could not parse /tmp/config.yml: x"),
        (OSError(13, "Permission denied"), "Permission denied"),
    ],
    ids=["RegistryError", "ConfigError", "OSError"],
)
def test_debug_env_var_emits_stderr_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exc: Exception,
    needle: str,
) -> None:
    monkeypatch.setenv("UNTAPED_COMPLETION_DEBUG", "1")
    _stub_entries(monkeypatch, exc)
    assert list(complete_workspace_name("a")) == []
    err = capsys.readouterr().err
    assert f"warning: completion: {type(exc).__name__}:" in err
    assert needle in err


@pytest.mark.parametrize("non_one_value", ["", "0", "false", "true", "yes"])
def test_only_strict_one_enables_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    non_one_value: str,
) -> None:
    # Strict equality with ``"1"`` — ``"true"`` / ``"yes"`` look truthy but
    # don't enable the diagnostic. Listed here precisely to pin that.
    monkeypatch.setenv("UNTAPED_COMPLETION_DEBUG", non_one_value)
    _stub_entries(monkeypatch, RegistryError("boom"))
    assert list(complete_workspace_name("a")) == []
    assert capsys.readouterr().err == ""
