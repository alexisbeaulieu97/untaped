"""Tests for settings only the home's base sets: never a project's file, never a profile.

A ``HomeOnly()`` mark makes a setting home-only, and so does placing a
``Dir(at=...)``: composition reads the place's setting from the declaration.
A run whose pick sets one is refused (exit 4) naming the key and what picked
the profile; ``config set --profile`` refuses it, ``config unset --profile``
still removes it, variables still apply, and doctor names a profile nobody picks.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

from core_test_support.commands.support import make_plugin
from untaped.runtime import bootstrap
from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.folder_layer import forget_folder_layer
from untaped.runtime.settings import get_settings
from untaped.sdk import Command, Output, key
from untaped.sdk._schema import walk_settings
from untaped.sdk.health import HomeOnly
from untaped.sdk.storage import Dir
from untaped.sdk.testing import CliResult, invoke_cli, make_project, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class AcmeSettings(BaseModel):
    """Where acme keeps its data, and what it greets with."""

    data_dir: Path | None = None
    machine: Annotated[str | None, HomeOnly()] = None
    greeting: str = "hello"


class Other(BaseModel):
    """Another plugin's settings."""

    elsewhere: Path | None = None


class Seen(BaseModel):
    data_dir: str
    greeting: str


class Show(Command[AcmeSettings]):
    """Show what acme reads."""

    output: Output[Seen] = Output()

    def run(self) -> Iterator[Seen]:
        yield Seen(data_dir=str(self.settings.data_dir), greeting=self.settings.greeting)


def _acme() -> type:
    return make_plugin(
        "acme",
        settings=AcmeSettings,
        data=Dir(at=key(AcmeSettings).data_dir),
        commands=(Show,),
    )


def _untaped(*args: str) -> CliResult:
    get_settings.cache_clear()
    forget_folder_layer()
    root = build_root_app(candidates=[plugin_candidate(_acme())])
    return invoke_cli(root.meta, list(args))


def _home(path: Path, text: str) -> None:
    path.write_text(f"format_version: 2\n{text}", encoding="utf-8")


@pytest.fixture
def work(_isolated_config: Path, tmp_path: Path) -> Path:
    """A home whose profile ``work`` sets ``acme.data_dir``, which only the base sets."""
    _home(
        _isolated_config,
        f"acme: {{data_dir: {tmp_path / 'base'}}}\n"
        f"profiles:\n  work: {{acme: {{data_dir: {tmp_path / 'work'}, greeting: hi}}}}\n"
        "  calm: {acme: {greeting: hey}}\n",
    )
    return _isolated_config


def _project(root: Path, monkeypatch: pytest.MonkeyPatch, profile: str) -> Path:
    make_project(root)
    file = root / ".untaped" / "config.yml"
    file.write_text(f"profile: {profile}\n", encoding="utf-8")
    file.chmod(0o600)
    file.parent.chmod(0o700)
    monkeypatch.chdir(root)
    return file


# --- marked by construction ------------------------------------------------------


def test_a_setting_a_dir_places_is_home_only_with_no_mark() -> None:
    result = bootstrap.compose_root(candidates=[plugin_candidate(_acme())])

    [block] = result.mount.declaration.plugin("acme").blocks
    marked = {d.key: d.home_only for d in walk_settings(AcmeSettings)}

    assert block.at == "data_dir"
    assert marked == {"data_dir": True, "machine": True, "greeting": False}


def test_a_dir_placed_at_another_models_setting_is_refused() -> None:
    stray = make_plugin("stray", settings=AcmeSettings, data=Dir(at=key(Other).elsewhere))

    result = bootstrap.compose_root(candidates=[plugin_candidate(stray)])

    [refused] = result.quarantine
    assert refused.reason == "bad-plugin-class"
    assert "a Dir's at= names one of the plugin's own settings" in refused.detail


# --- reading ---------------------------------------------------------------------


def test_a_profile_the_project_picks_is_refused_naming_the_key_and_the_file(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = _project(tmp_path / "api", monkeypatch, "work")

    result = _untaped("acme", "show")

    assert result.exit_code == 4
    assert "acme.data_dir can't be set in profile work (picked by " in result.stderr
    assert file.name in result.stderr and "only the home's base sets it" in result.stderr
    assert "untaped --profile work config unset acme.data_dir" in result.stderr


@pytest.mark.parametrize(
    ("args", "env", "picker"),
    [
        (("--profile", "work"), {}, "--profile"),
        ((), {"UNTAPED_PROFILE": "work"}, "UNTAPED_PROFILE"),
    ],
    ids=["flag", "variable"],
)
def test_a_profile_the_run_picks_is_refused(
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    env: dict[str, str],
    picker: str,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    result = _untaped(*args, "acme", "show")

    assert result.exit_code == 4
    assert f"acme.data_dir can't be set in profile work (picked by {picker})" in result.stderr


def test_an_explicit_mark_is_refused_in_a_profile_too(_isolated_config: Path) -> None:
    _home(_isolated_config, "profiles:\n  work: {acme: {machine: laptop}}\n")

    result = _untaped("--profile", "work", "acme", "show")

    assert result.exit_code == 4
    assert "acme.machine can't be set in profile work" in result.stderr


def test_a_profile_nobody_picks_fails_no_run(work: Path, tmp_path: Path) -> None:
    result = _untaped("acme", "show", "-f", "json")

    assert result.exit_code == 0, result.stderr
    assert str(tmp_path / "base") in result.stdout


def test_other_settings_layer_as_before_and_variables_still_apply(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_ACME__DATA_DIR", str(tmp_path / "variable"))

    result = _untaped("--profile", "calm", "acme", "show", "-f", "json")

    assert result.exit_code == 0, result.stderr
    assert f'"data_dir": "{tmp_path / "variable"}"' in result.stdout
    assert '"greeting": "hey"' in result.stdout


def test_doctor_names_a_profile_nobody_picks(work: Path) -> None:
    result = _untaped("doctor", "-f", "json")

    assert '"check": "untaped.home-only"' in result.stdout
    assert "acme.data_dir (profile work)" in result.stdout
    assert '"fix": ["--profile", "work", "config", "unset", "acme.data_dir"]' in result.stdout


# --- writing and fixing ----------------------------------------------------------


def test_config_set_into_a_profile_is_refused(work: Path, tmp_path: Path) -> None:
    refused = _untaped("--profile", "calm", "config", "set", "acme.data_dir", str(tmp_path))
    base = _untaped("config", "set", "acme.data_dir", str(tmp_path / "new"))

    assert refused.exit_code == 4
    assert "acme.data_dir can't be set in profile calm: only the home's base" in refused.stderr
    assert "leave out --profile to set it in the base" in refused.stderr
    assert base.exit_code == 0, base.stderr


def test_config_unset_fixes_the_profile_inside_the_project_that_picks_it(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path / "api", monkeypatch, "work")

    fixed = _untaped("--profile", "work", "config", "unset", "acme.data_dir")
    after = _untaped("acme", "show", "-f", "json")

    assert fixed.exit_code == 0, fixed.stderr
    assert after.exit_code == 0, after.stderr
    assert str(tmp_path / "base") in after.stdout and '"greeting": "hi"' in after.stdout


def test_config_unset_removes_one_of_two_home_only_values(
    _isolated_config: Path, tmp_path: Path
) -> None:
    _home(
        _isolated_config,
        f"profiles:\n  work: {{acme: {{data_dir: {tmp_path}, machine: laptop}}}}\n",
    )

    first = _untaped("--profile", "work", "config", "unset", "acme.data_dir")
    second = _untaped("--profile", "work", "config", "unset", "acme.machine")

    assert (first.exit_code, second.exit_code) == (0, 0), first.stderr + second.stderr


def test_profile_create_copy_leaves_home_only_settings_in_the_base(
    work: Path, tmp_path: Path
) -> None:
    created = _untaped("profile", "create", "copy", "--copy-from", "work")
    picked = _untaped("--profile", "copy", "acme", "show", "-f", "json")

    assert created.exit_code == 0, created.stderr
    assert "left out acme.data_dir: only the home's base sets it" in created.stderr
    assert picked.exit_code == 0, picked.stderr
    assert str(tmp_path / "base") in picked.stdout and '"greeting": "hi"' in picked.stdout


def test_a_section_the_copy_leaves_empty_is_left_out(work: Path) -> None:
    created = _untaped("profile", "create", "fresh", "--copy-from", "default")

    assert created.exit_code == 0, created.stderr
    assert "fresh: {}" in work.read_text(encoding="utf-8")
