"""The setup form for any plugin: every marked setting in its shape's field, every secret.

A plugin marks what it needs (``NeedsSetup``) and declares its credentials;
the form edits exactly those, writes the settings the user changed in one
change (validated together, refused when the profile changed since the form
opened), then files each credential under the key just saved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, HttpUrl

from screen.support import FakeHost
from test_management.stores import install_fake_stores
from test_management.support import compose, make_spec, write_config
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.management.setup_screen import SetupModel, SetupResult, setup_screen
from untaped.management.setup_state import ServiceState, setup_sections, setup_services
from untaped.management.setup_write import Candidate
from untaped.plugins.registry import PluginSpec
from untaped.screen.core import Key, Paste, Quit, Screen
from untaped.screen.runtime import Runtime
from untaped.sdk import Credential, CredentialKey, NeedsSetup, online_check
from untaped.settings import load_settings_section
from untaped.testing import drive_screen
from untaped.testing.screens import ScreenKey, ScreenRun
from untaped.theme import BUILTIN_THEMES
from untaped.token_store import pick_store

pytestmark = pytest.mark.usefixtures("_isolated_config")


class DuoServer(CredentialKey):
    """A server whose key needs two fields: they are only valid together."""

    url: HttpUrl
    org: str


class DuoProfile(BaseModel):
    """Section ``duo``: a two-field key, a marked number, and a setting the form leaves alone."""

    server: Annotated[DuoServer | None, NeedsSetup] = None
    port: Annotated[int | None, NeedsSetup] = None
    page_size: int = 50


DUO_TOKEN = Credential("duo", "token", key=DuoServer, env=("DUO_TOKEN",))
DUO_HOOK = Credential("duo", "webhook_secret", key=DuoServer)

#: What the online check saw: each credential's value, under the settings checked.
SEEN: list[tuple[str, str, int | None]] = []


def _probe() -> str:
    settings = load_settings_section("duo")
    key = settings.server
    SEEN.append(
        (
            DUO_TOKEN.get(key).get_secret_value(),
            DUO_HOOK.get(key).get_secret_value(),
            settings.port,
        )
    )
    return "authenticated"


def _duo() -> PluginSpec:
    return make_spec(
        "duo",
        settings=DuoProfile,
        credentials=(DUO_TOKEN, DUO_HOOK),
        doctor_checks=(online_check("duo.api", section="duo", probe=_probe),),
    )


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SEEN.clear()
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.delenv("DUO_TOKEN", raising=False)


def _build(profile: str = "default") -> Screen[SetupModel, SetupResult]:
    result = compose(_duo())
    raw = read_config_dict()
    return setup_screen(
        result,
        setup_services(result),
        sections=setup_sections(),
        profile=profile,
        store=pick_store(),
        profiles=sorted(raw.get("profiles") or ()),
    )


def _form(
    url: str | None = None, org: str | None = None, port: str | None = None
) -> list[ScreenKey]:
    """List -> rows -> form: URL, org and port (each replaced when given), then each secret
    typed into the store's tab."""
    keys: list[ScreenKey] = ["enter", "enter"]
    for value in (url, org, port):
        if value is not None:
            keys += ["ctrl-u", Paste(value)]
        keys.append("tab")
    return [*keys, "tab", Paste("tok"), "tab", "tab", Paste("hook"), "enter"]


def _section(path: Path, profile: str = "default") -> dict[str, Any]:
    profiles = read_config_dict(path).get("profiles") or {}
    node = (profiles.get(profile) or {}).get("duo", {})
    assert isinstance(node, dict)
    return node


def _result(run: ScreenRun[SetupModel, SetupResult]) -> SetupResult:
    assert isinstance(run.outcome, Quit), run.frame
    assert run.result is not None
    return run.result


def test_the_form_holds_each_marked_setting_and_each_secret() -> None:
    form = drive_screen(_build(), ["enter", "enter"]).model.forms["duo"]

    names = [name for name, _ in form.fields]

    # The key's two fields, the marked number, a source per credential; never page_size.
    assert names == [
        "server.url",
        "server.org",
        "port",
        "token:source",
        "webhook_secret:source",
        "buttons",
    ]


def test_saving_checks_both_secrets_then_files_each_under_the_key(_isolated_config: Path) -> None:
    run = drive_screen(_build(), [*_form("https://duo.example.com", "acme", "8443"), "esc", "esc"])

    assert SEEN == [("tok", "hook", 8443)]  # the check saw the candidates, nothing stored yet
    assert _section(_isolated_config) == {
        "server": {"url": "https://duo.example.com/", "org": "acme"},
        "port": 8443,
    }
    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="acme")
    assert DUO_TOKEN.get(key).get_secret_value() == "tok"
    assert DUO_HOOK.get(key).get_secret_value() == "hook"
    assert _result(run).touched == ("duo",)


def test_a_setting_the_plugin_needs_left_empty_is_refused(_isolated_config: Path) -> None:
    run = drive_screen(_build(), _form("https://duo.example.com", "acme"))

    assert "Enter the port." in run.frame
    assert SEEN == []
    assert not _isolated_config.exists()


def test_a_value_inherited_from_default_is_not_copied_into_the_profile(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    duo:\n"
        "      server: {url: 'https://duo.example.com', org: acme}\n      port: 8443\n",
    )

    drive_screen(_build("prod"), [*_form(), "esc", "esc"])

    # prod still inherits the server; only the logins were filed, under its key.
    assert "duo" not in (read_config_dict(_isolated_config)["profiles"]["prod"] or {})
    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="acme")
    assert DUO_TOKEN.get(key).get_secret_value() == "tok"


def _runtime() -> tuple[Runtime[SetupModel, SetupResult], FakeHost]:
    host = FakeHost()
    runtime = Runtime(_build(), host, theme=BUILTIN_THEMES["default"], size=(100, 40))
    runtime.start()
    return runtime, host


def _send(runtime: Runtime[SetupModel, SetupResult], host: FakeHost, keys: list[ScreenKey]) -> None:
    for key in keys:
        runtime.send(key if isinstance(key, Paste) else Key(key))
        host.deliver_all()


_SET_UP = (
    "profiles:\n  default:\n    duo:\n"
    "      server: {url: 'https://duo.example.com', org: acme}\n      port: 8443\n"
)


def test_a_value_changed_since_the_form_opened_refuses_the_whole_write(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _SET_UP)
    runtime, host = _runtime()
    # Someone else changes the org after the form opened.
    write_config(_isolated_config, _SET_UP.replace("acme", "other"))

    _send(runtime, host, _form(org="new-org"))

    assert _section(_isolated_config)["server"]["org"] == "other"
    assert "changed in profile default while it was open" in runtime.model.forms["duo"].error
    assert DUO_TOKEN.find(DuoServer(url=HttpUrl("https://duo.example.com"), org="new-org")) is None


def test_a_form_opened_before_a_relist_never_writes_back_its_stale_values(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _SET_UP)
    runtime, host = _runtime()
    # Someone else changes the port; a row write then relists the profile.
    write_config(_isolated_config, _SET_UP.replace("8443", "9000"))
    _send(runtime, host, ["enter", "down", "enter", "ctrl-u", Paste("10"), "enter"])

    _send(runtime, host, ["up", *_form(org="new-org")[1:]])

    # The form never showed 9000; it writes only the org it changed.
    assert _section(_isolated_config) == {
        "server": {"url": "https://duo.example.com", "org": "new-org"},
        "port": 9000,
        "page_size": 10,
    }


def test_a_save_that_failed_after_its_settings_were_written_can_be_retried(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _SET_UP)
    runtime, host = _runtime()
    store = Credential.store
    failures = ["the store is locked"]

    def flaky(self: Credential[Any], *args: Any, **kwargs: Any) -> Any:
        if self is DUO_HOOK and failures:
            raise ConfigError(failures.pop())
        return store(self, *args, **kwargs)

    monkeypatch.setattr(Credential, "store", flaky)
    _send(runtime, host, _form(org="new-org"))
    assert "the store is locked" in runtime.model.forms["duo"].error

    _send(runtime, host, ["enter"])  # Save again, the form as the user left it

    assert runtime.model.forms["duo"].error == ""
    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="new-org")
    assert DUO_HOOK.get(key).get_secret_value() == "hook"
    assert _section(_isolated_config)["server"]["org"] == "new-org"


def test_a_variable_that_sets_part_of_the_key_is_named_when_filing(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DUO__SERVER__ORG", "env-org")

    run = drive_screen(_build(), [*_form("https://duo.example.com", "acme", "8443"), "esc", "esc"])

    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="env-org")
    assert DUO_TOKEN.get(key).get_secret_value() == "tok"
    warnings = [text for level, text in _result(run).notes if level == "warning"]
    assert any("$UNTAPED_DUO__SERVER__ORG" in text and "env-org" in text for text in warnings)


def test_a_masked_setting_never_shows_in_a_repr() -> None:
    asked = Candidate("duo", "duo", "default", (("key", "hunter2"),), (), (("key", "old2"),))
    state = ServiceState({"key": "hunter2"}, own={"key": "old2"})

    assert "hunter2" not in repr(asked) + repr(state)
    assert "old2" not in repr(asked) + repr(state)
