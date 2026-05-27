"""Tab-completion callback behaviour for ``untaped workspace`` commands.

Pins the contract for ``complete_workspace_name``:

- Happy path returns names matching the incomplete prefix.
- Any :class:`UntapedError` raised on the registry-read path is
  swallowed so typer's completion machinery returns an empty list
  instead of a traceback the shell would discard silently.
- ``UNTAPED_COMPLETION_DEBUG=1`` opt-in turns the swallow into a
  single stderr line so users surprised by empty completions have a
  trail; any other env-var value keeps the silent default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError
from untaped_workspace.infrastructure import WorkspaceRegistryRepository


@pytest.fixture
def _silent_completion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_COMPLETION_DEBUG", raising=False)


def _stub_entries(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    if isinstance(behaviour, BaseException):

        def _raise(self: WorkspaceRegistryRepository) -> list[Workspace]:
            raise behaviour

        monkeypatch.setattr(WorkspaceRegistryRepository, "entries", _raise)
    else:

        def _return(self: WorkspaceRegistryRepository) -> list[Workspace]:
            return list(behaviour)

        monkeypatch.setattr(WorkspaceRegistryRepository, "entries", _return)


def test_happy_path_filters_by_prefix(
    monkeypatch: pytest.MonkeyPatch,
    _silent_completion_env: None,
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


def test_workspace_error_silent_by_default(
    monkeypatch: pytest.MonkeyPatch,
    _silent_completion_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_entries(monkeypatch, RegistryError("invalid workspace registry entry"))
    assert list(complete_workspace_name("a")) == []
    assert capsys.readouterr().err == ""


def test_config_error_silent_by_default(
    monkeypatch: pytest.MonkeyPatch,
    _silent_completion_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Today's `except WorkspaceError` lets ConfigError escape — this
    # regression-pins the broaden so a YAML typo in `~/.untaped/config.yml`
    # produces an empty completion list, not a swallowed traceback.
    _stub_entries(monkeypatch, ConfigError("could not parse /tmp/config.yml: …"))
    assert list(complete_workspace_name("a")) == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (RegistryError("invalid workspace registry entry"), "invalid workspace registry entry"),
        (ConfigError("could not parse /tmp/config.yml: x"), "could not parse /tmp/config.yml: x"),
    ],
    ids=["RegistryError", "ConfigError"],
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
    assert "untaped: completion: registry unreadable" in err
    assert needle in err


@pytest.mark.parametrize("falsy", ["", "0", "false", "true", "yes"])
def test_only_strict_one_enables_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    falsy: str,
) -> None:
    monkeypatch.setenv("UNTAPED_COMPLETION_DEBUG", falsy)
    _stub_entries(monkeypatch, RegistryError("boom"))
    assert list(complete_workspace_name("a")) == []
    assert capsys.readouterr().err == ""
