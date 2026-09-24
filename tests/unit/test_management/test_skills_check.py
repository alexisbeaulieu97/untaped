"""The per-run installed-skills check wired into the root shell.

After every root command, installed skills that no longer match this version
are reported on stderr; ``skills.updates`` switches the check to updating
them in place (``auto``) or silences it (``off``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_management.support import asset, make_spec, write_config
from untaped import bootstrap
from untaped.capability_api import SkillAsset
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _root(tmp_path: Path, *names: str) -> tuple[object, list[SkillAsset]]:
    skills = [asset(tmp_path, name) for name in names]
    root = bootstrap.build_root_app(
        builtins=(make_spec("demo", skills=tuple(skills)),), externals=()
    )
    return root, skills


def _run(root: object, *args: str) -> tuple[int, str]:
    result = CliInvoker().invoke(root.meta, list(args))  # type: ignore[attr-defined]
    return result.exit_code, result.output


def _installed(name: str) -> Path:
    return Path.home() / ".agents" / "skills" / name / "SKILL.md"


def _install_then_change(tmp_path: Path) -> object:
    root, skills = _root(tmp_path, "untaped-demo")
    assert _run(root, "skills", "install", "demo")[0] == 0
    skills[0].source.joinpath("SKILL.md").write_text("new instructions\n", encoding="utf-8")
    return root


def test_outdated_skill_warns_after_a_command(tmp_path: Path) -> None:
    root = _install_then_change(tmp_path)
    code, output = _run(root, "demo", "--help")
    assert code == 0
    assert "warning: installed skills are out of date: untaped-demo" in output
    assert "hint: run `untaped skills update`" in output
    assert "skills.updates" in output


def test_no_warning_when_skills_match(tmp_path: Path) -> None:
    root, _ = _root(tmp_path, "untaped-demo")
    assert _run(root, "skills", "install", "demo")[0] == 0
    assert "warning" not in _run(root, "demo", "--help")[1]


def test_failed_command_still_warns(tmp_path: Path) -> None:
    root = _install_then_change(tmp_path)
    code, output = _run(root, "demo", "--no-such-flag")
    assert code == 2
    assert "installed skills are out of date" in output


@pytest.mark.parametrize("args", [("skills", "list"), ("doctor",), ("--version",)])
def test_skills_and_doctor_commands_do_not_warn(tmp_path: Path, args: tuple[str, ...]) -> None:
    root = _install_then_change(tmp_path)
    assert "installed skills are out of date" not in _run(root, *args)[1]


def test_off_silences_the_warning(_isolated_config: Path, tmp_path: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    skills: {updates: 'off'}\n")
    root = _install_then_change(tmp_path)
    assert "warning" not in _run(root, "demo", "--help")[1]
    assert _installed("untaped-demo").read_text() != "new instructions\n"


def test_auto_updates_outdated_skills_in_place(_isolated_config: Path, tmp_path: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    skills: {updates: auto}\n")
    root = _install_then_change(tmp_path)
    code, output = _run(root, "demo", "--help")
    assert code == 0
    assert "updated 1 outdated skill" in output
    assert "warning" not in output
    assert _installed("untaped-demo").read_text() == "new instructions\n"


def test_env_var_selects_the_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UNTAPED_SKILLS__UPDATES", "off")
    root = _install_then_change(tmp_path)
    assert "warning" not in _run(root, "demo", "--help")[1]


def test_unshipped_skill_warns_but_is_never_removed(_isolated_config: Path, tmp_path: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    skills: {updates: auto}\n")
    old_root, _ = _root(tmp_path, "untaped-gone")
    assert _run(old_root, "skills", "install", "gone")[0] == 0
    root, _ = _root(tmp_path, "untaped-demo")
    output = _run(root, "demo", "--help")[1]
    assert "warning: installed skills are no longer shipped: untaped-gone" in output
    assert "hint: run `untaped skills remove untaped-gone`" in output
    assert _installed("untaped-gone").is_file()
