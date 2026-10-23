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

from core_test_support.commands.stores import install_fake_stores
from core_test_support.commands.support import compose, make_spec, write_config
from core_test_support.screen.support import FakeHost
from untaped.core.commands import setup_write
from untaped.core.commands.setup_screen import SetupModel, SetupResult, setup_screen
from untaped.core.commands.setup_state import ServiceState, setup_sections, setup_services
from untaped.core.commands.setup_write import Candidate
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import PluginSpec
from untaped.runtime.settings import load_settings_section
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.screen.core import Key, Paste, Quit, Screen
from untaped.screen.runtime import Runtime
from untaped.sdk import Credential, CredentialKey, NeedsSetup, online_check
from untaped.sdk.errors import ConfigError
from untaped.sdk.network._token_store import pick_store
from untaped.sdk.testing import drive_screen
from untaped.sdk.testing.screens import ScreenKey, ScreenRun

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

    assert "enter the port" in run.frame
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
    form = runtime.model.forms["duo"]
    assert "changed in profile default while it was open" in form.error
    assert dict(form.fields)["server.org"].value == "other"  # it opens again on the file
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


def test_a_save_that_failed_before_writing_still_refuses_a_value_changed_since(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _SET_UP)
    runtime, host = _runtime()
    write_config(_isolated_config, _SET_UP.replace("8443", "9000"))  # someone else
    ensure = setup_write.ensure_profile
    failures = ["the disk is full"]

    def flaky(profile: str) -> Any:
        if failures:
            raise ConfigError(failures.pop())
        return ensure(profile)

    monkeypatch.setattr(setup_write, "ensure_profile", flaky)
    _send(runtime, host, _form(port="7000"))
    assert "the disk is full" in runtime.model.forms["duo"].error

    _send(runtime, host, ["enter"])  # Save again

    assert _section(_isolated_config)["port"] == 9000
    assert "changed in profile default while it was open" in runtime.model.forms["duo"].error


def test_a_refused_value_is_shown_on_its_field_which_takes_the_focus() -> None:
    run = drive_screen(_build(), _form("duo.example.com", "acme", "8443"), size=(100, 20))

    form = run.model.forms["duo"]
    assert form.focus == 0  # the URL, scrolled back into sight
    assert form.error == ""
    assert dict(form.fields)["server.url"].error.startswith("invalid value for 'duo.server.url'")
    assert "invalid value for 'duo.server.url'" in run.frame
    assert SEEN == []

    fixed = drive_screen(
        _build(),
        [
            *_form("duo.example.com", "acme", "8443"),
            "ctrl-u",
            Paste("https://duo.example.com"),
            "ctrl-s",
        ],
    )

    assert SEEN == [("tok", "hook", 8443)]  # fixed in place, then checked and saved
    assert fixed.model.forms["duo"].error == ""
    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="acme")
    assert DUO_TOKEN.get(key).get_secret_value() == "tok"


def test_a_setting_left_empty_is_named_on_its_field_on_a_short_screen() -> None:
    run = drive_screen(_build(), _form("https://duo.example.com", "acme"), size=(100, 20))

    assert "enter the port" in run.frame
    assert run.model.forms["duo"].focus == 2


def test_a_variable_that_sets_part_of_the_key_is_named_when_filing(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DUO__SERVER__ORG", "env-org")

    run = drive_screen(_build(), [*_form("https://duo.example.com", "acme", "8443"), "esc", "esc"])

    key = DuoServer(url=HttpUrl("https://duo.example.com"), org="env-org")
    assert DUO_TOKEN.get(key).get_secret_value() == "tok"
    warnings = [text for level, text in _result(run).notes if level == "warning"]
    assert len(warnings) == 1  # once for the key both logins share
    assert "$UNTAPED_DUO__SERVER__ORG" in warnings[0]
    assert "env-org" in warnings[0]


def test_a_masked_setting_never_shows_in_a_repr() -> None:
    asked = Candidate("duo", "duo", "default", (("key", "hunter2"),), (), (("key", "old2"),))
    state = ServiceState({"key": "hunter2"}, own={"key": "old2"})

    assert "hunter2" not in repr(asked) + repr(state)
    assert "old2" not in repr(asked) + repr(state)


class RosterProfile(BaseModel):
    """Section ``roster``: a list the plugin needs."""

    teams: Annotated[list[str] | None, NeedsSetup] = None


def test_an_empty_list_the_plugin_needs_is_refused(_isolated_config: Path) -> None:
    result = compose(make_spec("roster", settings=RosterProfile))
    screen = setup_screen(
        result, setup_services(result), sections=setup_sections(), profile="default", store=None
    )

    run = drive_screen(screen, ["enter", "enter", "ctrl-s"])

    assert "enter the teams" in run.frame
    assert not _isolated_config.exists()
