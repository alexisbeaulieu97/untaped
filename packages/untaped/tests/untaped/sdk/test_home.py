"""The home: ``UNTAPED_HOME`` moves config.yml, state.yml and every plugin's folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.runtime.settings import plugins_root, resolve_state_path
from untaped.sdk import home, plugin_path
from untaped.sdk._home import config_path
from untaped.sdk.errors import ConfigError


def test_the_home_is_dot_untaped_as_spelled_unless_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_HOME", raising=False)
    assert home() == Path("~/.untaped")
    assert plugin_path("acme", "db") == Path("~/.untaped/plugins/acme/db")
    assert config_path() == Path.home() / ".untaped" / "config.yml"


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_home_is_unset(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("UNTAPED_HOME", blank)
    assert home() == Path("~/.untaped")


def test_untaped_home_moves_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    assert home() == tmp_path
    assert config_path() == tmp_path / "config.yml"
    assert resolve_state_path() == tmp_path / "state.yml"
    assert plugins_root() == tmp_path / "plugins"
    assert plugin_path("acme") == tmp_path / "plugins" / "acme"


def test_a_home_under_tilde_is_kept_as_spelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "~/work-home")
    assert home() == Path("~/work-home")
    assert config_path() == Path.home() / "work-home" / "config.yml"


def test_a_relative_home_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "rel/home")
    with pytest.raises(ConfigError, match="UNTAPED_HOME must be an absolute path") as caught:
        config_path()
    assert caught.value.exit_code == 4
