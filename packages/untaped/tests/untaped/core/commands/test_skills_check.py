"""The per-run installed-skills check wired into the root app.

After every root command, home-folder stubs an older untaped wrote are
refreshed (``untaped.skills.updates: auto``, the default), reported on stderr
(``warn``) or left alone (``off``). Which stubs count: ``test_skills_stubs.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import age_stub, make_plugin, write_config
from untaped.core.commands import skills as skills_cli
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Confirm, DryRun, Plugin
from untaped.sdk.errors import ConfigError
from untaped.sdk.plugin import StubSkill
from untaped.sdk.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _root(name: str) -> object:
    spec = make_plugin(name.removeprefix("untaped-"), skill=f"Teach agents how to use {name}.")
    return bootstrap.build_root_app(candidates=(plugin_candidate(spec),))


def _run(root: object, *args: str) -> tuple[int, str]:
    result = CliInvoker().invoke(root.meta, list(args))  # type: ignore[attr-defined]
    return result.exit_code, result.output


def _installed(name: str) -> Path:
    return Path.home() / ".agents" / "skills" / name / "SKILL.md"


_OLDER = "older wording\n"


@pytest.fixture
def _warn(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: warn}\n")


def _install_then_age() -> object:
    root = _root("untaped-demo")
    assert _run(root, "skills", "install", "demo")[0] == 0
    age_stub(_installed("untaped-demo").parent, text=_OLDER)
    return root


@pytest.mark.usefixtures("_warn")
def test_outdated_skill_warns_after_a_command() -> None:
    root = _install_then_age()
    code, output = _run(root, "demo", "--help")
    assert code == 0
    assert "warning: agent skills from an older untaped: untaped-demo" in output
    assert "hint: run `untaped skills update`" in output
    assert "untaped.skills.updates" in output


@pytest.mark.usefixtures("_warn")
def test_the_warning_follows_the_command_s_json_format() -> None:
    root = _install_then_age()
    result = CliInvoker().invoke(root.meta, ["profile", "list", "--format", "json"])  # type: ignore[attr-defined]

    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    assert [line["level"] for line in lines] == ["warning", "hint"]
    assert "untaped-demo" in lines[0]["message"]


def test_no_warning_when_skills_match() -> None:
    root = _root("untaped-demo")
    assert _run(root, "skills", "install", "demo")[0] == 0
    assert "warning" not in _run(root, "demo", "--help")[1]


@pytest.mark.usefixtures("_warn")
def test_failed_command_still_warns() -> None:
    root = _install_then_age()
    code, output = _run(root, "demo", "--no-such-flag")
    assert code == 2
    assert "agent skills from an older untaped" in output


@pytest.mark.usefixtures("_warn")
@pytest.mark.parametrize(
    "args",
    [
        ("skills", "list"),
        ("doctor",),
        ("--version",),
        ("Skills", "list"),
        ("DOCTOR",),
        ("skills",),
        ("skills", "--help"),
        ("doctor", "--help"),
        ("skills", "install", "--bogus"),
        ("setup", "plan"),
        ("setup", "prune", "--dry-run"),
        ("setup", "--help"),
    ],
)
def test_skills_and_doctor_commands_do_not_warn(args: tuple[str, ...]) -> None:
    root = _install_then_age()
    assert "from an older untaped" not in _run(root, *args)[1]


def test_off_silences_the_warning(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: 'off'}\n")
    root = _install_then_age()
    assert "warning" not in _run(root, "demo", "--help")[1]
    assert _installed("untaped-demo").read_text() == _OLDER


def test_auto_refreshes_outdated_skills_in_place(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: auto}\n")
    root = _install_then_age()
    code, output = _run(root, "demo", "--help")
    assert code == 0
    assert "warning" not in output
    assert "`untaped docs demo`" in _installed("untaped-demo").read_text()


def test_auto_warns_about_a_stub_it_could_not_refresh(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: auto}\n")
    root = _install_then_age()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConfigError("could not update it")

    monkeypatch.setattr(skills_cli, "update_installed_skill", broken)
    output = _run(root, "demo", "--help")[1]
    assert "warning: agent skills from an older untaped: untaped-demo" in output
    assert _installed("untaped-demo").read_text() == _OLDER


def test_env_var_selects_the_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__SKILLS__UPDATES", "warn")
    root = _install_then_age()
    assert "agent skills from an older untaped" in _run(root, "demo", "--help")[1]
    assert _installed("untaped-demo").read_text() == _OLDER


@pytest.mark.parametrize("mode", ["warn", "auto"])
def test_unshipped_skill_is_left_to_doctor(_isolated_config: Path, mode: str) -> None:
    # Another environment (``uvx untaped`` without plugins) may lack the plugin
    # that ships it, so the per-run check cannot tell it is gone for good.
    write_config(_isolated_config, f"format_version: 2\nuntaped: {{skills: {{updates: {mode}}}}}\n")
    assert _run(_root("untaped-gone"), "skills", "install", "gone")[0] == 0
    root = _root("untaped-demo")
    output = _run(root, "demo", "--help")[1]
    assert "untaped-gone" not in output
    assert "warning" not in output
    assert _installed("untaped-gone").is_file()


class _Apply(Command):
    """Apply, asking first."""

    confirm: Confirm
    check: bool = Field(False, description="Preview through the confirmation.")

    def run(self) -> None:
        confirm = self.confirm.as_dry_run() if self.check else self.confirm
        confirm("Apply?", needed=False)


class _Move(Command):
    """Move, never asking."""

    dry_run: DryRun

    def run(self) -> None:
        pass


class _Repair(Command):
    """Repair the setup."""

    repairs = True

    def run(self) -> None:
        pass


class _DemoSettings(BaseModel):
    pass


class _Demo(Plugin):
    """A 12.0 demo plugin."""

    settings = _DemoSettings
    commands = (_Apply, _Move, _Repair)
    skill = StubSkill("Teach agents how to use untaped-demo.")


def _declared_root() -> object:
    candidate = PluginCandidate(distribution="untaped-demo", name="demo", target=_Demo)
    root = bootstrap.build_root_app(candidates=(candidate,))
    assert _run(root, "skills", "install", "demo")[0] == 0
    age_stub(_installed("untaped-demo").parent, text=_OLDER)
    return root


@pytest.mark.parametrize(
    ("args", "updated"),
    [
        (("demo", "apply", "--dry-run"), False),
        (("demo", "apply", "--yes"), True),
        (("demo", "apply", "--check"), False),
        (("demo", "move", "--dry-run"), False),
        (("demo", "move"), True),
        (("demo", "repair"), False),
    ],
    ids=["confirm-previews", "confirm-writes", "check", "dry-run-previews", "writes", "repairs"],
)
def test_a_declared_command_says_whether_the_check_may_write(
    _isolated_config: Path, args: tuple[str, ...], updated: bool
) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: auto}\n")
    root = _declared_root()
    code, output = _run(root, *args)
    assert code == 0, output
    assert (_installed("untaped-demo").read_text() != _OLDER) is updated


@pytest.mark.usefixtures("_warn")
def test_a_repairing_command_does_not_warn() -> None:
    root = _declared_root()
    assert "from an older untaped" not in _run(root, "demo", "repair")[1]
    assert "from an older untaped" in _run(root, "demo", "move")[1]


def test_a_check_that_finds_nothing_is_still_a_preview(_isolated_config: Path) -> None:
    write_config(_isolated_config, "format_version: 2\nuntaped:\n  skills: {updates: auto}\n")
    root = _install_then_age()
    code, output = _run(root, "project", "prune", "--check")
    assert code == 0, output
    assert _installed("untaped-demo").read_text() == _OLDER
