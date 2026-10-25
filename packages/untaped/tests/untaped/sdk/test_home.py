"""The home: ``UNTAPED_HOME`` moves config.yml, state.yml and every plugin's folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.runtime.settings import plugins_root, resolve_state_path
from untaped.sdk import home, plugin_path
from untaped.sdk._home import config_path
from untaped.sdk.errors import ConfigError
from untaped.sdk.projects import _reserved


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


def test_an_unknown_user_s_tilde_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HOME", "~no-such-user-untaped/home")
    with pytest.raises(ConfigError, match="unknown user's home"):
        home()


@pytest.mark.parametrize("name", ["UNTAPED_CONFIG", "UNTAPED_STATE"])
def test_a_retired_variable_still_set_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, str(tmp_path / "config.yml"))
    with pytest.raises(ConfigError, match=f"{name} is no longer read") as caught:
        config_path()
    assert "UNTAPED_HOME" in (caught.value.hint or "")


def test_a_checkout_holding_a_moved_home_can_be_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = (tmp_path / "repo").resolve()
    monkeypatch.setenv("UNTAPED_HOME", str(checkout / ".untaped-home"))
    assert _reserved(checkout) is None
    assert _reserved(checkout / ".untaped-home") is not None
    assert _reserved((Path.home() / ".untaped").resolve()) is not None


def test_a_project_s_own_folder_cannot_be_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = (tmp_path / "repo").resolve()
    monkeypatch.setenv("UNTAPED_HOME", str(checkout / ".untaped"))
    assert "would hold" in (_reserved(checkout) or "")
