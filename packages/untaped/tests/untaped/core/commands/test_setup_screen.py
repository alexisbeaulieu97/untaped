"""The ``untaped setup`` screen: what each key does and what ends up on disk.

Every case drives the real screen with ``drive_screen`` (commands run
synchronously) or, where timing matters, a ``Runtime`` over a host that holds
its jobs. Config and token stores are the isolated config and the fake
``pass`` / ``secret-tool`` executables, so nothing real is read or written.
"""

from __future__ import annotations

import json
import re
import stat
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import HttpUrl

from core_test_support.commands.stores import FakeStores, install_fake_stores
from core_test_support.commands.support import (
    ENVY_TOKEN,
    FAIL,
    PROBES,
    WIZ_TOKEN,
    EnvProfile,
    EnvyServer,
    LegacyProfile,
    UnmarkedWizProfile,
    WizProfile,
    WizServer,
    compose,
    make_spec,
    wiz_api_check,
    write_config,
)
from core_test_support.screen.support import FakeHost
from untaped.core.commands.setup_connection import token_choices as _token_choices
from untaped.core.commands.setup_screen import (
    CapRow,
    SetupModel,
    SetupResult,
    setup_screen,
)
from untaped.core.commands.setup_state import (
    ServiceState,
    setup_sections,
    setup_services,
)
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import PluginSpec
from untaped.runtime.settings import active_overlay, get_settings
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.screen.core import Cancel, Key, Paste, Quit, Resize, Screen
from untaped.screen.runtime import Runtime
from untaped.sdk import Credential, online_check
from untaped.sdk._app_context import app_context
from untaped.sdk.errors import ConfigError
from untaped.sdk.network._token_store import pick_store
from untaped.sdk.storage import _refs as refs
from untaped.sdk.testing import ScreenKeys, current_memory_store, drive_screen
from untaped.sdk.testing.screens import ScreenKey, ScreenRun, rendered_text

pytestmark = pytest.mark.usefixtures("_isolated_config")

SECRET = "s3cr3t-t0ken"


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    """No store is usable unless a test installs one; no probe has run."""
    PROBES.clear()
    FAIL.clear()
    return install_fake_stores(tmp_path, monkeypatch)


def _wiz() -> PluginSpec:
    return make_spec("wiz", settings=WizProfile, doctor_checks=(wiz_api_check(),))


def _envy() -> PluginSpec:
    return make_spec("envy", settings=EnvProfile)


def _legacy() -> PluginSpec:
    return make_spec("legacy", settings=LegacyProfile)


def _build(
    *specs: PluginSpec, profile: str = "default", only: list[str] | None = None
) -> Screen[SetupModel, SetupResult]:
    result = compose(*(specs or (_wiz(),)))
    services = setup_services(result, only)
    raw = read_config_dict()
    return setup_screen(
        result,
        services,
        sections=setup_sections(only),
        profile=profile,
        store=pick_store(),
        profiles=sorted(raw.get("profiles") or ()),
    )


def _keys(
    url: str | None = None,
    *,
    right: int = 0,
    token: str | None = None,
    command: str | None = None,
    submit: bool = True,
) -> list[ScreenKey]:
    """List -> rows -> setup form: the URL, the token tab (``right`` steps in), its field,
    then enter."""
    keys: list[ScreenKey] = ["enter", "enter"]
    if url is not None:
        keys += ["ctrl-u", Paste(url)]
    keys += ["tab", *(["right"] * right)]
    if token is not None:
        keys += ["tab", Paste(token)]
    if command is not None:
        keys += ["tab", Paste(command)]
    return [*keys, "enter"] if submit else keys


def _flat(text: str) -> str:
    """A frame without its box lines and line breaks, so wrapped sentences can be found."""
    return " ".join(re.sub(r"[│╭╮╰╯─━╌]", " ", text).split())


def _wiz_section(path: Path, profile: str = "default") -> dict[str, Any]:
    section = read_config_dict(path)["profiles"][profile]["wiz"]
    assert isinstance(section, dict)
    return section


def _token_program(tmp_path: Path, *, token: str = "tok", exit_code: int = 0) -> str:
    """An executable that prints ``token`` (or fails), standing in for ``op read ...``."""
    script = tmp_path / "bin" / "print-token"
    script.parent.mkdir(exist_ok=True)
    body = f"import sys\nprint({token!r})\nsys.exit({exit_code})\n"
    script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _login(url: str, *, envy: bool = False) -> str | None:
    """The token a run gets for ``url``'s server (``wiz``'s, or ``envy``'s), if any."""
    found = (
        ENVY_TOKEN.find(EnvyServer(url=HttpUrl(url)))
        if envy
        else WIZ_TOKEN.find(WizServer(url=HttpUrl(url)))
    )
    return None if found is None else found.get_secret_value()


def _result(run: ScreenRun[SetupModel, SetupResult]) -> SetupResult:
    assert isinstance(run.outcome, Quit), run.frame
    assert run.result is not None
    return run.result


# --- writes: each token branch writes what the wizard wrote ----------------------


def test_check_passes_then_writes_a_stored_token(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")

    run = drive_screen(_build(), [*_keys("https://wiz", token=" tok "), "esc", "esc"])

    assert _wiz_section(_isolated_config) == {"server": {"url": "https://wiz/"}}
    assert list(current_memory_store().entries.values()) == ["tok"]
    assert _login("https://wiz") == "tok"
    assert PROBES == ["probed"]
    assert run.commands_run == ("probe", "save")
    assert _result(run) == SetupResult("default", ("wiz",), (), interrupted=False)


def test_check_passes_then_writes_a_typed_token_for_a_model_without_a_command(
    _isolated_config: Path,
) -> None:
    run = drive_screen(_build(_legacy()), [*_keys("https://legacy", token="tok"), "esc", "esc"])

    section = read_config_dict(_isolated_config)["profiles"]["default"]["legacy"]
    assert section == {"base_url": "https://legacy", "token": "tok"}
    assert _result(run).touched == ("legacy",)


def test_check_passes_then_writes_a_command(_isolated_config: Path, tmp_path: Path) -> None:
    program = _token_program(tmp_path)
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: default\n")

    # No store is installed: the tabs are Command and Env, so the command is the first.
    run = drive_screen(
        _build(), [*_keys("https://wiz", command=f"{program} --flag 'two words'"), "esc", "esc"]
    )

    assert _wiz_section(_isolated_config) == {"server": {"url": "https://wiz/"}}
    assert _login("https://wiz") == "tok"
    assert run.commands_run == ("prime", "probe", "save")
    assert PROBES == ["probed"]


def test_check_passes_then_writes_the_env_choice(_isolated_config: Path) -> None:
    run = drive_screen(_build(_envy()), [*_keys("https://envy", right=1), "esc", "esc"])

    assert read_config_dict(_isolated_config)["profiles"]["default"]["envy"] == {
        "server": {"url": "https://envy/"}
    }
    assert ("info", "export $ENVY_TOKEN in your shell for untaped envy to use it") in (
        _result(run).notes
    )


def _seed(stores: FakeStores, tmp_path: Path, entries: dict[str, str]) -> None:
    """Entries already in the fake store (and, for ``pass``, their ``.gpg`` files)."""
    stores.state.write_text(json.dumps(entries), encoding="utf-8")
    for key in entries:
        gpg = tmp_path / "password-store" / f"{key}.gpg"
        gpg.parent.mkdir(parents=True, exist_ok=True)
        gpg.write_text("encrypted", encoding="utf-8")


_UNMARKED_STORED = (
    "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n"
    "      token_command: [pass, show, untaped/default/wiz]\n"
)


def test_an_11_0_section_using_the_env_var_drops_its_stored_token(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(_isolated_config, _UNMARKED_STORED)
    _seed(stores, tmp_path, {"untaped/default/wiz": "tok"})

    # keep, store, command, env: the env tab is the fourth.
    run = drive_screen(
        _build(make_spec("wiz", settings=UnmarkedWizProfile)), [*_keys(right=3), "esc", "esc"]
    )

    assert _wiz_section(_isolated_config) == {"base_url": "https://wiz"}
    assert stores.entries() == {}
    notes = [text for _, text in _result(run).notes]
    assert "deleted the replaced wiz token from pass (untaped/default/wiz)" in notes


def test_an_unreachable_replaced_store_only_warns(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "secret-tool", "pass")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n"
        "      token_command:\n"
        "        [secret-tool, lookup, service, untaped, account, default/wiz]\n",
    )
    stores.state.write_text('{"default/wiz": "old"}')
    monkeypatch.setenv("STUB_MODE", "no-service")  # secret-tool goes away: pass takes over

    run = drive_screen(
        _build(make_spec("wiz", settings=UnmarkedWizProfile)),
        [*_keys(right=1, token="new"), "esc", "esc"],
    )

    notes = _result(run).notes
    assert any(
        kind == "warning" and "could not delete the replaced wiz token from secret-tool" in text
        for kind, text in notes
    )
    assert stores.entries() == {"default/wiz": "old", "untaped/default/wiz": "new"}


def test_a_stray_token_line_is_not_offered_to_move(_isolated_config: Path) -> None:
    # wiz's settings declare no `token`, so the line is not read (doctor names it).
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      server: {url: https://wiz}\n      token: old\n",
    )

    run = drive_screen(_build())

    state = run.model.states["wiz"]
    assert (state.plaintext, state.own_command) == (None, None)


def test_check_passes_then_keeps_the_current_token(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIZ_TOKEN", "kept")
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz:\n      server: {url: https://old}\n"
    )

    run = drive_screen(_build(), [*_keys("https://new"), "esc", "esc"])

    assert _wiz_section(_isolated_config) == {"server": {"url": "https://new/"}}
    assert _result(run).touched == ("wiz",)


# --- the check comes first -------------------------------------------------------


def test_check_fails_then_nothing_is_written(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(_build(), _keys("https://wiz", token="bad"))

    assert run.commands_run == ("probe",)
    assert not _isolated_config.exists()
    assert stores.entries() == {}
    flat = _flat(run.frame)
    assert "HTTP 401 from https://wiz/me" in flat
    assert "Save anyway" in flat and "Cancel" in flat
    assert "failed" in flat  # the status of the plugin


def test_save_anyway_writes(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    # After the failed check the focus is on its buttons, on Cancel: Save anyway is left of it.
    run = drive_screen(
        _build(), [*_keys("https://wiz", token="bad"), "left", "enter", "esc", "esc"]
    )

    assert run.commands_run == ("probe", "save")
    assert list(current_memory_store().entries.values()) == ["bad"]
    assert _login("https://wiz") == "bad"
    assert _wiz_section(_isolated_config) == {"server": {"url": "https://wiz/"}}
    assert "saved, check failed" in _flat(run.frames[-2])
    assert _result(run).touched == ("wiz",)


def test_a_failed_check_is_forgotten_when_the_values_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(_build(), [*_keys("https://wiz", token="bad"), "shift-tab", "x"])

    flat = _flat(run.frame)
    assert "Save anyway" not in flat
    assert "HTTP 401" not in flat
    assert "Save" in flat


def test_cancel_forgets_the_failed_check_and_returns_to_the_rows(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(_build(), [*_keys("https://wiz", token="bad"), "enter"])

    assert run.model.focus == "section"
    assert run.model.failed == {}
    assert "Save anyway" not in _flat(run.frame)
    assert not _isolated_config.exists()


def test_only_online_probes_run_before_saving(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline: list[str] = []
    from untaped.sdk.health.doctor import DoctorCheck, DoctorResult

    def offline_run(_ctx: object) -> DoctorResult:
        offline.append("ran")
        return DoctorResult(id="wiz.offline", ok=True, detail="x")

    spec = make_spec(
        "wiz",
        settings=WizProfile,
        doctor_checks=(
            DoctorCheck(id="wiz.offline", title="offline", run=offline_run),  # type: ignore[arg-type]
            wiz_api_check(),
        ),
    )
    install_fake_stores(tmp_path, monkeypatch, "pass")

    drive_screen(_build(spec), _keys("https://wiz", token="tok"))

    assert offline == []
    assert PROBES == ["probed"]


def test_a_plugin_without_an_online_check_saves_directly(_isolated_config: Path) -> None:
    run = drive_screen(_build(_legacy()), [*_keys("https://legacy", token="tok")])

    assert run.commands_run == ("probe", "save")
    assert "saved" in _flat(run.frame)
    assert _wiz_section_for(_isolated_config, "legacy")["token"] == "tok"


def _wiz_section_for(path: Path, section: str) -> dict[str, Any]:
    node = read_config_dict(path)["profiles"]["default"][section]
    assert isinstance(node, dict)
    return node


def test_a_failing_token_command_is_the_check_result(
    _isolated_config: Path, tmp_path: Path
) -> None:
    program = _token_program(tmp_path, exit_code=3)

    run = drive_screen(_build(), _keys("https://wiz", command=program))

    assert run.commands_run == ("prime",)  # no probe: the command itself failed
    assert PROBES == []
    assert "exited with status 3" in _flat(run.frame)
    assert "Save anyway" in _flat(run.frame)
    assert not _isolated_config.exists()


def test_a_malformed_token_command_writes_nothing(_isolated_config: Path) -> None:
    run = drive_screen(_build(), _keys("https://wiz", command='pass show "wiz'))

    assert run.commands_run == ()
    assert "invalid wiz token command" in _flat(run.frame)
    assert not _isolated_config.exists()


def test_an_empty_url_and_an_empty_token_are_refused_in_place(_isolated_config: Path) -> None:
    run = drive_screen(_build(_legacy()), _keys("", token=""))

    assert run.commands_run == ()
    flat = _flat(run.frame)
    assert "Enter the base URL." in flat and "Enter a token." in flat
    assert "Base URL" in flat
    assert not _isolated_config.exists()


def test_the_overlay_never_writes_and_never_changes_get_settings(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz:\n      server: {url: https://old}\n"
    )
    config = _isolated_config.read_bytes()
    seen: list[tuple[str | None, str | None]] = []

    def probe() -> str:
        # A cross-section read: it goes through get_settings(), which the overlay bypasses.
        wiz = get_settings().wiz  # type: ignore[attr-defined]
        server = app_context().section("wiz", WizProfile).server
        assert server is not None
        seen.append((str(wiz.server.url), WIZ_TOKEN.get(server).get_secret_value()))
        return "ok"

    spec = make_spec(
        "wiz",
        settings=WizProfile,
        doctor_checks=(online_check("wiz.api", section="wiz", probe=probe),),
    )
    FAIL.clear()
    screen = _build(spec)
    host = FakeHost(threads=True)
    runtime = Runtime(screen, host, theme=BUILTIN_THEMES["default"], size=(100, 30))
    runtime.start()
    for key in _keys("https://new", token="candidate", submit=False):
        runtime.send(_message(key))
    runtime.send(Key("ctrl-s"))
    host.deliver()  # the probe, on a worker thread, saw the candidate values
    assert seen == [("https://new/", "candidate")]
    # The main thread never saw them (the check dropped the cache; it was never filled).
    main = get_settings().wiz  # type: ignore[attr-defined]
    assert (str(main.server.url), WIZ_TOKEN.find(main.server)) == ("https://old/", None)
    assert active_overlay() is None
    host.deliver()  # ... then the write ran, on its own thread
    host.join()

    # The write went through the repository (the cache was dropped by it, not filled).
    assert _wiz_section(_isolated_config) == {"server": {"url": "https://new/"}}
    assert list(current_memory_store().entries.values()) == ["candidate"]
    assert config != _isolated_config.read_bytes()


def _message(key: ScreenKey) -> object:
    return key if isinstance(key, Paste | Resize) else Key(key)


def test_a_failed_check_leaves_the_files_and_the_cache_alone(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config, "profiles:\n  default:\n    wiz:\n      server: {url: https://old}\n"
    )
    config = _isolated_config.read_bytes()
    FAIL.append(True)
    screen = _build()  # building tests the store once; nothing after that touches it
    calls = list(stores.calls())

    drive_screen(screen, _keys("https://new", token="bad"))

    assert _isolated_config.read_bytes() == config
    assert stores.entries() == {} and stores.calls() == calls
    assert current_memory_store().entries == {}
    after = get_settings().wiz  # type: ignore[attr-defined]
    assert (str(after.server.url), WIZ_TOKEN.find(after.server)) == ("https://old/", None)


# --- timing: one check at a time, quitting during a save --------------------------


def _held(
    screen: Screen[SetupModel, SetupResult],
) -> tuple[Runtime[SetupModel, SetupResult], FakeHost]:
    host = FakeHost()
    runtime = Runtime(screen, host, theme=BUILTIN_THEMES["default"], size=(100, 30))
    runtime.start()
    return runtime, host


def _send(runtime: Runtime[SetupModel, SetupResult], keys: Sequence[ScreenKey]) -> None:
    for key in keys:
        runtime.send(_message(key))


def test_a_save_while_a_check_runs_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    runtime, host = _held(_build())
    _send(runtime, _keys("https://wiz", token="tok"))
    assert runtime.model.phase == "probing"

    # Another try while the check runs: back to the list, in again, ctrl-s, enter.
    _send(runtime, ["esc", "enter", "ctrl-s", "enter", "tab", "enter"])
    runtime.send(Key("ctrl-s"))

    assert host.spawned == ["background"]  # still the one probe
    host.deliver_all()
    assert PROBES == ["probed"]
    assert host.spawned == ["background", "write"]


def test_a_second_form_cannot_start_a_check_while_one_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    runtime, host = _held(_build(_legacy(), _wiz()))  # listed by name: legacy, then wiz
    _send(runtime, _keys("https://legacy", token="tok"))
    assert runtime.model.phase == "probing"

    _send(runtime, ["esc", "esc", "down", *_keys("https://wiz", token="x")])

    assert host.spawned == ["background"]  # the second form never started a check
    assert runtime.model.phase == "probing"
    assert runtime.model.name == "wiz"  # (it is only selected)


@pytest.mark.parametrize("quit_key", ["esc", "ctrl-c"])
def test_quitting_during_a_save_leaves_a_complete_write(
    quit_key: str, _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    program = _token_program(tmp_path)
    runtime, host = _held(_build())
    _send(runtime, _keys("https://new", right=1, command=program))
    for _ in range(2):  # the prime, then the check
        host.run_jobs()
        host.deliver()
    assert runtime.model.phase == "saving"
    assert host.spawned[-1] == "write"  # held: it has not run

    _send(runtime, ["esc", "esc", quit_key])  # out of the form, the rows, then quit
    assert runtime.outcome is None  # the screen waits for the write
    assert host.finishes == 0
    host.run_jobs()
    host.deliver()

    assert isinstance(runtime.outcome, Quit)
    result = runtime.outcome.result
    assert result.touched == ("wiz",)
    assert result.interrupted is (quit_key == "ctrl-c")
    assert _wiz_section(_isolated_config) == {"server": {"url": "https://new/"}}
    assert _login("https://new") == "tok"
    assert host.finishes == 1


def test_a_write_that_fails_while_quitting_is_reported_not_lost(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "gpg-roundtrip")
    runtime, host = _held(_build())
    _send(runtime, _keys("https://wiz", token="tok"))
    host.run_jobs()
    host.deliver()
    assert runtime.model.phase == "saving"

    _send(runtime, ["esc", "esc", "esc"])
    host.run_jobs()
    host.deliver()

    assert isinstance(runtime.outcome, Quit)
    result = runtime.outcome.result
    assert result.touched == ()
    assert any(
        kind == "warning" and text.startswith("could not save wiz: gpg decrypt failed")
        for kind, text in result.notes
    )
    assert not _isolated_config.exists()


# --- quitting --------------------------------------------------------------------


def test_esc_on_the_list_quits_with_the_touched_set(_isolated_config: Path) -> None:
    run = drive_screen(_build(), ["esc"])

    assert isinstance(run.outcome, Quit)  # never a Cancel
    assert run.result == SetupResult("default", (), (), interrupted=False)


def test_ctrl_c_on_the_list_quits_interrupted(_isolated_config: Path) -> None:
    run = drive_screen(_build(_legacy()), [*_keys("https://legacy", token="tok"), "ctrl-c"])

    assert not isinstance(run.outcome, Cancel)
    assert _result(run) == SetupResult("default", ("legacy",), (), interrupted=True)


def test_ctrl_c_in_a_field_quits_interrupted_too() -> None:
    run = drive_screen(_build(), ["enter", "ctrl-c"])

    assert _result(run).interrupted is True


def test_esc_in_the_form_goes_back_to_the_list_first() -> None:
    run = drive_screen(_build(), ["enter", "esc"])

    assert run.outcome is None
    assert run.model.focus == "list"


# --- profile ---------------------------------------------------------------------


def test_new_profile_is_created_once(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: default\n")
    screen = _build(_legacy(), _envy(), profile="prod")  # listed by name: envy, then legacy

    run = drive_screen(
        screen,
        [
            *_keys("https://envy", right=1),
            "esc",
            "down",
            *_keys("https://legacy", token="t"),
            "esc",
            "esc",
        ],
    )

    result = _result(run)
    assert result.profile == "prod"
    assert result.touched == ("envy", "legacy")
    assert [text for _, text in result.notes].count("created profile: prod") == 1
    config = read_config_dict(_isolated_config)
    assert config["active"] == "default"
    assert set(config["profiles"]["prod"]) == {"legacy", "envy"}


def test_preflight_failure_writes_nothing(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    monkeypatch.setenv("STUB_MODE", "gpg-roundtrip")
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: default\n")
    config = _isolated_config.read_bytes()

    run = drive_screen(_build(profile="prod"), _keys("https://wiz", token="tok"))

    assert run.commands_run == ("probe", "save")
    assert "gpg decrypt failed for the pass store" in _flat(run.frame)
    assert stores.entries() == {}
    assert _isolated_config.read_bytes() == config  # not even the profile
    assert run.model.saved == ()
    assert "failed" in _flat(run.frame)


def test_changing_the_profile_reloads_the_forms(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      server: {url: https://default.example}\n"
        "  prod:\n    wiz:\n      server: {url: https://prod.example}\n",
    )

    run = drive_screen(_build(), ["enter", "esc", "shift-tab", "ctrl-u", Paste("prod"), "enter"])

    assert run.commands_run == ("load_profile",)
    assert run.model.current == "prod"
    assert run.model.focus == "list"
    assert "https://default.example" in run.frames[1]
    assert "https://prod.example" in _flat(run.frame)


def test_an_empty_profile_name_is_refused_in_place() -> None:
    run = drive_screen(_build(), ["shift-tab", "ctrl-u", "enter"])

    assert run.commands_run == ()
    assert "Enter a profile name." in run.frame


def test_the_profile_field_completes_existing_profiles(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  production: {}\n")

    run = drive_screen(_build(), ["shift-tab", "ctrl-u", "p"])

    assert "production" in run.frame


def test_open_profile_completions_keep_the_frame_on_screen(_isolated_config: Path) -> None:
    profiles = "".join(f"  p{n}: {{}}\n" for n in range(8))
    write_config(_isolated_config, f"profiles:\n  default: {{}}\n{profiles}")

    run = drive_screen(_build(), ["shift-tab", "ctrl-u", "p"], size=(200, 24))  # header on one line

    lines = run.frame.splitlines()
    assert "p0" in run.frame
    assert len(lines) <= 24
    assert any(line.startswith("╰") and "╯╰" in line for line in lines), run.frame  # both panes
    assert "? help" in lines[-1]


# --- what the screen shows -------------------------------------------------------


def test_a_status_per_state(_isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_READY__TOKEN", "t")
    specs = [
        make_spec(
            name, settings=WizProfile, credentials=(Credential(name, "token", key=WizServer),)
        )
        for name in ("ready", "bare", "fresh", "broken")
    ]
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    ready: {server: {url: https://r}}\n"
        "    bare: {server: {url: https://b}}\n    broken: {server: {url: [1, 2]}}\n",
    )

    frame = drive_screen(_build(*specs)).frame

    for name, status in (
        ("ready", "configured"),
        ("bare", "missing token"),
        ("fresh", "not configured"),
        ("broken", "invalid settings"),
    ):
        assert re.search(rf"{name}\s+{status}", frame), frame
    # Those that still need something come first, in the order the services are listed.
    names = [row.name for row in drive_screen(_build(*specs)).model.rows[1:5]]
    assert names == ["bare", "broken", "fresh", "ready"]


def test_progress_statuses_use_the_ellipsis_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    runtime, host = _held(_build())
    _send(runtime, _keys("https://wiz", token="tok"))

    checking = rendered_text(runtime.renderable(), 100, 30)
    host.run_jobs()
    host.deliver()
    saving = rendered_text(runtime.renderable(), 100, 30)

    assert re.search(r"wiz\s+checking…", checking)
    assert re.search(r"wiz\s+saving…", saving)


def test_inherited_token_note_shows_in_the_tab(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    wiz:\n      token: shared\n  prod: {}\nactive: default\n",
    )

    unmarked = make_spec("wiz", settings=UnmarkedWizProfile)
    run = drive_screen(_build(unmarked, profile="prod"), ["enter", "enter", "tab"], size=(220, 40))

    flat = _flat(run.frame)
    assert "would leave it in charge, so only keeping the current token is offered" in flat
    assert "untaped auth migrate" in flat
    assert "Keep" in flat and "Command" not in flat and "pass" not in flat


_UNSET = ServiceState(values={})


def test_tabs_offer_the_same_choices_as_before(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    store = pick_store()

    def offered(
        state: ServiceState, spec: PluginSpec | None = None, *, has_command: bool = True
    ) -> list[str]:
        spec = spec or _wiz()
        return [c.value for c in _token_choices(spec, state, store, has_command=has_command)]

    plain = ServiceState(values={}, secret_sources={"token": "wiz.token"}, plaintext="old")
    assert offered(plain) == ["move", "keep", "store", "command", "env"]
    assert offered(_UNSET) == ["store", "command", "env"]
    inherited = ServiceState(values={}, secret_sources={"token": "wiz.token"}, inherited_token=True)
    # A stored login answers before default's plain text, so every choice works.
    assert offered(inherited) == ["keep", "store", "command", "env"]
    assert [c.value for c in _token_choices(_wiz(), _UNSET, None, has_command=True)] == [
        "command",
        "env",
    ]
    unmarked = make_spec("wiz", settings=UnmarkedWizProfile)
    assert offered(inherited, unmarked) == ["keep"]
    assert offered(_UNSET, _legacy(), has_command=False) == ["store", "enter"]
    assert offered(ServiceState(values={}, inherited_command=True), unmarked) == [
        "store",
        "command",
    ]


def test_without_a_store_the_tabs_never_offer_plain_text() -> None:
    run = drive_screen(_build(_envy()), ["enter", "enter", "tab"], size=(110, 32))

    flat = _flat(run.frame)
    assert "Command" in flat and "Env" in flat
    assert "Enter" not in flat.split("Token source", 1)[1].split("Token")[0]


def test_the_tab_labels_name_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")

    run = drive_screen(_build(), ["enter", "enter", "tab"])

    assert re.search(r"pass\s+Command\s+Env", run.frame)


# --- secrets never show ----------------------------------------------------------


@pytest.mark.parametrize("branch", ["store", "enter", "move", "command"])
def test_token_never_in_a_frame(
    branch: str, _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    keys: list[ScreenKey]
    spec = _wiz()
    if branch == "enter":
        spec, keys = _legacy(), _keys("https://legacy", token=SECRET)
    elif branch == "store":
        keys = _keys("https://wiz", token=SECRET)
    elif branch == "move":
        # Only an 11.0 section still reads a plaintext token from the config.
        spec = make_spec("wiz", settings=UnmarkedWizProfile)
        write_config(
            _isolated_config,
            "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n"
            f"      token: {SECRET}\n",
        )
        keys = _keys()
    else:
        keys = _keys("https://wiz", right=1, command=_token_program(tmp_path, token=SECRET))

    # The unfinished run goes first: the finished one migrates a plaintext token, and the
    # unfinished one must start from the original config (the token in the model's states).
    failing = drive_screen(_build(spec), keys[:-1])  # typed, not submitted
    run = drive_screen(_build(spec), keys)

    if branch == "move":
        # The precondition: the model does hold the plaintext, so only its repr keeps it out.
        assert failing.model.states["wiz"].plaintext == SECRET
    for each in (run, failing):
        assert all(SECRET not in frame for frame in each.frames)
        assert SECRET not in repr(each.model)
    assert SECRET not in repr(run.result)


def test_a_failed_check_never_shows_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(_build(), _keys("https://wiz", token=SECRET))

    assert all(SECRET not in frame for frame in run.frames)
    assert SECRET not in repr(run.model)  # the candidate is kept for Save anyway


# --- commands --------------------------------------------------------------------


def test_the_command_tab_runs_the_token_command_as_a_suspend_command(tmp_path: Path) -> None:
    program = _token_program(tmp_path)
    runtime, host = _held(_build())

    _send(runtime, _keys("https://wiz", command=program))
    host.run_jobs()
    host.deliver()
    host.run_jobs()
    host.deliver()
    host.run_jobs()
    host.deliver()

    assert host.spawned == ["suspend", "background", "write"]


def test_the_token_command_is_primed_before_the_background_check(tmp_path: Path) -> None:
    program = _token_program(tmp_path, token="primed-value")
    from untaped.sdk.network.auth import clear_token_cache

    clear_token_cache()
    seen: list[str] = []

    def probe() -> str:
        server = app_context().section("wiz", WizProfile).server
        assert server is not None
        seen.append(WIZ_TOKEN.get(server).get_secret_value())  # cached by the suspended command
        return "ok"

    spec = make_spec(
        "wiz",
        settings=WizProfile,
        doctor_checks=(online_check("wiz.api", section="wiz", probe=probe),),
    )

    drive_screen(_build(spec), _keys("https://wiz", command=program))

    assert seen == ["primed-value"]


# --- looks -----------------------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(BUILTIN_THEMES))
def test_every_theme_draws_the_screen(
    theme: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(
        _build(),
        [*_keys("https://wiz", token="bad"), "tab"],
        theme=BUILTIN_THEMES[theme],
        size=(100, 30),
    )

    assert all(len(line) <= 100 for frame in run.frames for line in frame.splitlines())
    if theme == "plain":
        assert all(frame.isascii() for frame in run.frames)


def test_plain_theme_frames_are_ascii(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")

    run = drive_screen(
        _build(_wiz(), _envy()),
        [*_keys("https://wiz", token="tok"), "down", "enter", "tab", "right", "?"],
        theme=BUILTIN_THEMES["plain"],
    )

    assert all(frame.isascii() for frame in run.frames), run.frame


def test_the_screen_is_usable_on_a_narrow_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")

    run = drive_screen(_build(), ["enter", "tab", Resize(60, 24)], size=(100, 30))

    assert all(len(line) <= 100 for line in run.frames[-1].splitlines())
    assert "Setup" in run.frame and "wiz" in run.frame


def test_scripted_keys_run_the_screen_end_to_end(_isolated_config: Path) -> None:
    keys = ScreenKeys(*_keys("https://legacy", token="tok"), "esc", "esc")

    run = drive_screen(_build(_legacy()), keys)

    assert _result(run).touched == ("legacy",)


# --- the profile field and the forms agree ---------------------------------------


_TWO_PROFILES = (
    "profiles:\n  default:\n    wiz:\n      server: {url: https://default.example}\n"
    "  prod:\n    wiz:\n      server: {url: https://prod.example}\n"
)


@pytest.mark.parametrize("key", ["tab", "shift-tab"])
def test_leaving_the_profile_field_loads_what_is_typed(_isolated_config: Path, key: str) -> None:
    write_config(_isolated_config, _TWO_PROFILES)

    run = drive_screen(_build(), ["shift-tab", "ctrl-u", Paste("prod"), key])

    assert run.commands_run == ("load_profile",)
    assert run.model.current == "prod"
    assert run.model.profile.value == "prod"
    assert "https://prod.example" in _flat(run.frame)


def test_ctrl_s_in_the_profile_field_loads_it_and_never_saves_to_the_old_profile(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _TWO_PROFILES)
    config = _isolated_config.read_bytes()

    run = drive_screen(_build(_legacy()), ["shift-tab", "ctrl-u", Paste("prod"), "ctrl-s"])

    assert run.commands_run == ("load_profile",)
    assert run.model.current == "prod"
    assert run.model.saved == ()
    assert _isolated_config.read_bytes() == config


def test_saving_after_leaving_the_profile_field_writes_the_typed_profile(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _TWO_PROFILES)

    run = drive_screen(
        _build(_legacy()),
        [
            "shift-tab",
            "ctrl-u",
            Paste("prod"),
            "tab",
            *_keys("https://legacy", token="t"),
            "esc",
            "esc",
        ],
    )

    assert _result(run).profile == "prod"
    config = read_config_dict(_isolated_config)["profiles"]
    assert config["prod"]["legacy"]["base_url"] == "https://legacy"
    assert "legacy" not in config["default"]


def test_leaving_an_unchanged_or_empty_profile_field_does_not_load() -> None:
    same = drive_screen(_build(), ["shift-tab", "tab"])
    assert (same.commands_run, same.model.focus) == ((), "list")
    stay = drive_screen(_build(), ["shift-tab", "shift-tab"])
    assert (stay.commands_run, stay.model.focus) == ((), "profile")
    empty = drive_screen(_build(), ["shift-tab", "ctrl-u", "tab"])
    assert empty.commands_run == () and empty.model.focus == "profile"
    assert "Enter a profile name." in empty.frame


@pytest.mark.parametrize("phase", ["loading", "probing", "saving"])
def test_the_profile_field_cannot_be_edited_while_a_check_a_save_or_a_load_runs(phase: str) -> None:
    screen = _build()
    on_field = drive_screen(screen, ["shift-tab"]).model
    assert on_field.focus == "profile"
    busy = replace(on_field, phase=phase)  # type: ignore[arg-type]

    for message in (Key("x"), Paste("y")):
        assert screen.update(busy, message) == (busy, [])

    # The same messages do edit it when nothing runs.
    typed, _ = screen.update(on_field, Key("x"))
    assert typed.profile.value == "defaultx"


# --- Save anyway is per plugin -----------------------------------------------


def test_save_anyway_survives_another_plugin_saving(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)  # listed by name: envy, then wiz; only wiz has an online check
    keys: list[ScreenKey] = [
        "down",
        *_keys("https://wiz", token="bad"),  # wiz fails its check
        "esc",
        "esc",
        "up",
        *_keys("https://envy", right=2),  # envy saves (it has no online check): the Env tab
        "esc",
        "down",
        "enter",
        "enter",  # back in wiz's form, on its buttons
        "left",  # Save anyway
        "enter",
    ]

    run = drive_screen(_build(_envy(), _wiz()), keys)

    assert _wiz_section_for(_isolated_config, "envy")["server"] == {"url": "https://envy/"}
    assert _wiz_section(_isolated_config)["server"] == {"url": "https://wiz/"}
    assert run.model.saved == (("default", "envy"), ("default", "wiz"))
    assert run.model.failed == {}


def test_a_failure_is_kept_for_each_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    run = drive_screen(_build(_legacy(), _wiz()), ["down", *_keys("https://wiz", token="bad")])

    assert set(run.model.failed) == {"wiz"}
    assert "Save anyway" in _flat(run.frame)


def _pressed_save_anyway(
    model: SetupModel, screen: Screen[SetupModel, SetupResult]
) -> tuple[SetupModel, list[Any]]:
    from untaped.screen.components.buttons import Pressed

    updated, cmds = screen.update(model, Pressed("save_anyway"))
    return updated, list(cmds)


def test_save_anyway_does_nothing_unless_this_plugin_failed_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)
    screen = _build(_legacy(), _wiz())
    failed = drive_screen(screen, ["down", *_keys("https://wiz", token="bad")]).model
    assert set(failed.failed) == {"wiz"}

    # It writes for the failed plugin ...
    written, cmds = _pressed_save_anyway(failed, screen)
    assert [cmd.name for cmd in cmds] == ["save"] and written.phase == "saving"
    # ... not while a check or a save runs ...
    busy = replace(failed, phase="probing")
    assert _pressed_save_anyway(busy, screen) == (busy, [])
    # ... and not for a plugin that did not fail (a stale button), even though a candidate
    # is pending: here wiz failed, legacy is selected.
    other = replace(failed, selected=1)  # row 0 is this install
    assert other.name == "legacy" and other.pending is not None and "legacy" not in other.failed
    assert _pressed_save_anyway(other, screen) == (other, [])
    nothing = replace(failed, failed={})
    assert nothing.pending is not None
    assert _pressed_save_anyway(nothing, screen) == (nothing, [])


# --- the footer ------------------------------------------------------------------


@pytest.mark.parametrize("width", [100, 80, 60])
def test_the_footer_names_what_the_keys_do_and_keeps_back_and_help(width: int) -> None:
    on_list = drive_screen(_build(), [], size=(width, 30))
    in_rows = drive_screen(_build(), ["enter"], size=(width, 30))
    in_form = drive_screen(_build(), ["enter", "enter"], size=(width, 30))
    in_profile = drive_screen(_build(), ["shift-tab"], size=(width, 30))

    for run in (on_list, in_rows, in_form, in_profile):
        assert run.frame.splitlines()[-1].rstrip().endswith("esc back · ? help"), run.frame
    if width >= 100:
        assert "enter open" in on_list.frame.splitlines()[-1]
        assert "enter open" in in_rows.frame.splitlines()[-1]  # the connection row
        assert "ctrl-s" not in on_list.frame.splitlines()[-1]  # rows write a field at a time
    if width >= 80:
        assert "ctrl-s save" in in_form.frame.splitlines()[-1]
        assert "enter load" in in_profile.frame.splitlines()[-1]
        assert "ctrl-s" not in in_profile.frame.splitlines()[-1]


# --- every check runs the token command ------------------------------------------


def test_a_retry_runs_the_token_command_again(tmp_path: Path) -> None:
    from untaped.sdk.network.auth import clear_token_cache

    clear_token_cache()
    runs = tmp_path / "runs"
    script = tmp_path / "bin" / "count-token"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\np = Path({str(runs)!r})\n"
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\nprint('tok')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    FAIL.append(True)  # the first check rejects the token

    # Submit, then submit the same values again (focus is on the command field).
    run = drive_screen(_build(), [*_keys("https://wiz", command=str(script)), "ctrl-s"])

    assert runs.read_text() == "xx"
    assert run.commands_run == ("prime", "probe", "prime", "probe")
    clear_token_cache()


# --- this install ----------------------------------------------------------------


def _about(model: SetupModel) -> str:
    """The text of this install's form."""
    return str(dict(model.forms["this install"].fields)["about"].text)  # type: ignore[attr-defined]


def test_the_first_row_is_this_install_and_the_cursor_starts_on_a_plugin() -> None:
    run = drive_screen(_build(), [], size=(120, 30))
    assert run.model.rows[0] == CapRow("this install", "not registered")
    assert run.model.name == "wiz"


def test_register_on_the_install_row_registers_and_says_so() -> None:
    opened = drive_screen(_build(), ["up", "enter"], size=(120, 30))
    assert "Register" in opened.frame
    assert "so its runs remove the store layouts" in _about(opened.model)
    assert "ctrl-s register" in opened.frame.splitlines()[-1]

    run = drive_screen(_build(), ["up", "enter", "enter", "esc"], size=(120, 30))

    assert refs.this_install() is not None
    assert any(text.startswith("registered this install") for _, text in _result(run).notes)
    after = drive_screen(_build(), ["up", "enter"], size=(120, 30))
    assert after.model.rows[0].status == "registered"
    assert _about(after.model).startswith("Registered: untaped")
    assert "ctrl-s" not in after.frame.splitlines()[-1]


def test_enter_on_the_list_only_opens_the_row() -> None:
    drive_screen(_build(), ["up", "enter", "esc", "esc"], size=(120, 30))
    assert refs.this_install() is None


def test_a_failed_registration_shows_under_the_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> object:
        raise ConfigError("refs/ is read-only")

    monkeypatch.setattr(refs, "register_this_install", refuse)
    run = drive_screen(_build(), ["up", "enter", "enter"], size=(120, 30))
    assert run.model.rows[0].status == "not registered"
    assert run.model.forms["this install"].error == (
        "could not register this install: refs/ is read-only"
    )


def test_a_source_tree_says_so_and_offers_no_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refs, "environment", lambda: None)
    run = drive_screen(_build(), ["up", "enter"], size=(120, 30))
    assert run.model.rows[0].status == "source tree"
    assert "only an installed untaped can be registered" in _about(run.model)
    assert "Register" not in run.frame
