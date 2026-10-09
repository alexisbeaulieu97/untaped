"""``untaped setup``: the command around the setup screen.

The screen itself (keys, checks, what each token source writes) is tested in
``test_setup_screen.py``. These cases run the command end to end through
``invoke_cli``, with the screen scripted by keys or replaced by its result, and
cover what the command adds: the terminal requirement, what it prints after the
screen closes (notes, doctor rows, the closing line) and its exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_management.stores import FakeStores, install_fake_stores
from test_management.support import (
    FAIL,
    PROBES,
    EnvProfile,
    ExtProfile,
    WizProfile,
    make_spec,
    wiz_api_check,
    write_config,
)
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.management.setup_screen import SetupResult
from untaped.screen.core import Cancel, Paste
from untaped.testing import (
    CliResult,
    ScreenKeys,
    ScriptedPromptBackend,
    invoke_cli,
    provider_candidate,
)
from untaped.testing.screens import ScreenKey

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _wiz_spec() -> Any:
    return make_spec("wiz", settings=WizProfile, doctor_checks=(wiz_api_check(),))


def _setup(backend: ScriptedPromptBackend | None, *args: str, terminal: bool = True) -> CliResult:
    plain = make_spec("plain", settings=ExtProfile)
    root = bootstrap.build_root_app(
        candidates=(provider_candidate(_wiz_spec()), provider_candidate(plain))
    )
    return invoke_cli(
        root.meta,
        ["setup", "--format", "json", *args],
        interactive=backend is not None,
        prompt_backend=backend,
        terminal=terminal,
    )


def _keys(url: str, *, token: str | None = None, right: int = 0) -> list[ScreenKey]:
    """List -> form: the URL, the token tab (``right`` steps in), its field, then save."""
    keys: list[ScreenKey] = ["enter", "ctrl-u", Paste(url), "tab", *(["right"] * right)]
    if token is not None:
        keys += ["tab", Paste(token)]
    return [*keys, "enter"]


def _scripted(*keys: ScreenKey) -> ScriptedPromptBackend:
    return ScriptedPromptBackend(screens=[ScreenKeys(*keys)])


@pytest.fixture(autouse=True)
def _reset_probes() -> None:
    PROBES.clear()
    FAIL.clear()


@pytest.fixture(autouse=True)
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeStores:
    """No store is usable unless a test installs one."""
    return install_fake_stores(tmp_path, monkeypatch)


def _wiz(path: Path, profile: str = "default") -> dict[str, Any]:
    section = read_config_dict(path)["profiles"][profile]["wiz"]
    assert isinstance(section, dict)
    return section


def test_setup_without_a_terminal_is_a_usage_error(_isolated_config: Path) -> None:
    result = _setup(None, terminal=False)

    assert result.exit_code == 2
    assert "`untaped setup` needs a terminal; use `untaped setup plan --format json`" in (
        result.stderr
    )
    assert not _isolated_config.exists()


def test_setup_without_a_terminal_touches_neither_the_config_nor_the_keychain(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")

    def touched(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read before the terminal was checked")

    monkeypatch.setattr("untaped.management.setup.service_store", touched)
    monkeypatch.setattr("untaped.management.setup.read_config_dict", touched)

    result = _setup(None, terminal=False)

    assert result.exit_code == 2
    assert "needs a terminal" in result.stderr
    assert stores.calls() == []
    assert not _isolated_config.exists()


def test_setup_configures_the_service_and_prints_its_checks_after_the_screen(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    backend = _scripted(*_keys("https://wiz", token=" tok "), "esc")

    result = _setup(backend)

    assert result.exit_code == 0, result.output
    assert _wiz(_isolated_config) == {
        "base_url": "https://wiz",
        "token_command": ["pass", "show", "untaped/default/wiz"],
    }
    assert stores.entries() == {"untaped/default/wiz": "tok"}
    assert [screen.title for screen in backend.ran] == ["Set up untaped"]
    # The record of the run is on stdout, after the screen: the doctor rows of what it set up.
    rows = {row["check"]: row for row in json.loads(result.stdout)}
    assert rows["wiz.api"] == {
        "check": "wiz.api",
        "plugin": "wiz",
        "status": "pass",
        "title": "wiz API reachable",
        "detail": "authenticated as alice",
        "fix": None,
        "automatic": False,
    }
    assert {row["plugin"] for row in rows.values()} == {"wiz"}
    assert PROBES == ["probed", "probed"]  # the check before saving, then the doctor row
    assert "profile default is ready" in result.stderr


def test_setup_ends_with_the_checklist(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\nactive: default\n")
    backend = _scripted("enter", "ctrl-u", Paste("https://wiz"), "tab", "right", "enter", "esc")
    root = bootstrap.build_root_app(candidates=(provider_candidate(_wiz_spec()),))

    result = invoke_cli(
        root.meta, ["setup"], interactive=True, prompt_backend=backend, terminal=True
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "wiz"
    assert any(line.split()[:2] == ["✓", "wiz.api"] for line in lines)
    assert "\nsetup: " in result.stderr


def test_a_failed_check_saved_anyway_fails_setup_and_names_the_fix(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)
    # The check fails; tab goes to the buttons and enter saves anyway.
    backend = _scripted(*_keys("https://wiz", token="bad"), "tab", "enter", "esc")

    result = _setup(backend)

    assert result.exit_code == 1
    row = next(row for row in json.loads(result.stdout) if row["check"] == "wiz.api")
    assert row["fix"] == ["--profile", "default", "auth", "set", "wiz"]
    assert "setup:" in result.stderr and "1 fail" in result.stderr
    assert stores.entries() == {"untaped/default/wiz": "bad"}


def test_a_failed_check_cancelled_writes_nothing(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)
    backend = _scripted(*_keys("https://wiz", token="bad"), "tab", "right", "enter", "esc")

    result = _setup(backend)

    assert result.exit_code == 0, result.output
    assert "nothing saved; no changes made" in result.stderr
    assert stores.entries() == {}
    assert not _isolated_config.exists()


def test_leaving_without_saving_changes_nothing(_isolated_config: Path) -> None:
    result = _setup(_scripted("esc"))

    assert result.exit_code == 0, result.output
    assert "nothing saved; no changes made" in result.stderr
    assert result.stdout == ""
    assert not _isolated_config.exists()


def test_ctrl_c_after_a_save_prints_what_was_saved_and_exits_130(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")

    result = _setup(_scripted(*_keys("https://wiz", token="tok"), "ctrl-c"))

    assert result.exit_code == 130
    assert _wiz(_isolated_config)["base_url"] == "https://wiz"
    assert any(row["check"] == "wiz.api" for row in json.loads(result.stdout))


def test_ctrl_c_wins_over_a_failed_row(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_stores(tmp_path, monkeypatch, "pass")
    FAIL.append(True)

    result = _setup(_scripted(*_keys("https://wiz", token="bad"), "tab", "enter", "ctrl-c"))

    assert result.exit_code == 130
    row = next(row for row in json.loads(result.stdout) if row["check"] == "wiz.api")
    assert row["status"] == "fail"  # the record is still printed


def test_ctrl_c_with_nothing_saved_exits_130(_isolated_config: Path) -> None:
    result = _setup(_scripted("ctrl-c"))

    assert result.exit_code == 130
    assert not _isolated_config.exists()


def test_a_cancelled_screen_exits_1(_isolated_config: Path) -> None:
    result = _setup(ScriptedPromptBackend(screens=[Cancel()]))

    assert result.exit_code == 1
    assert not _isolated_config.exists()


def test_only_lists_just_those_services(_isolated_config: Path) -> None:
    backend = _scripted("esc")

    _setup(backend, "--only", "wiz")

    screen = backend.ran[0]
    model, _ = screen.init()
    assert [row.name for row in model.rows] == ["wiz"]


def test_only_rejects_a_name_that_is_not_a_service(_isolated_config: Path) -> None:
    result = _setup(ScriptedPromptBackend(), "--only", "plain")

    assert result.exit_code == 2
    assert "service not found: 'plain'; known: wiz" in result.stderr


# --- post-processing of the screen's result ---------------------------------------


def test_notes_print_in_order_before_the_rows(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n")
    done = SetupResult(
        "default",
        ("wiz",),
        (("success", "created profile: prod"), ("info", "export $WIZ_TOKEN in your shell")),
    )

    result = _setup(ScriptedPromptBackend(screens=[done]))

    assert result.exit_code == 0, result.output
    assert result.stderr.index("created profile: prod") < result.stderr.index("export $WIZ_TOKEN")
    assert result.stderr.index("export $WIZ_TOKEN") < result.stderr.index("setup:")


def test_a_profile_other_than_the_active_one_says_how_to_use_it(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default: {}\n  prod:\n    wiz:\n      base_url: https://wiz.prod\n"
        "active: default\n",
    )

    result = _setup(ScriptedPromptBackend(screens=[SetupResult("prod", ("wiz",), ())]))

    assert result.exit_code == 0, result.output
    assert "profile prod is ready" in result.stderr
    assert "untaped --profile prod" in result.stderr
    assert "untaped profile use prod" in result.stderr


def test_nothing_touched_prints_the_notes_and_no_rows(_isolated_config: Path) -> None:
    result = _setup(ScriptedPromptBackend(screens=[SetupResult("default", (), (("info", "hi"),))]))

    assert result.exit_code == 0, result.output
    assert "hi" in result.stderr and "nothing saved; no changes made" in result.stderr
    assert result.stdout == ""


def test_rows_cover_only_the_touched_plugins(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    wiz:\n      base_url: https://wiz\n")
    envy = make_spec("envy", settings=EnvProfile)
    root = bootstrap.build_root_app(
        candidates=(provider_candidate(_wiz_spec()), provider_candidate(envy))
    )
    backend = ScriptedPromptBackend(screens=[SetupResult("default", ("wiz",), ())])

    result = invoke_cli(
        root.meta,
        ["setup", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
        terminal=True,
    )

    assert {row["plugin"] for row in json.loads(result.stdout)} == {"wiz"}
