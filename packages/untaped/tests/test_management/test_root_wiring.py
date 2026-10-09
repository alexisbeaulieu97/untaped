"""Integration tests for the Wave 1.4 root management surface.

The unified root mounts exactly the five management commands (no plugin
subtrees ship in 1.4 — workspace mounts in 1.5), reachable through the
position-independent root-option dispatch, with the §4 Jira-isolation case
covered end to end through the real surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_management.support import (
    ExtProfile,
    GithubProfile,
    JiraProfile,
    asset,
    check,
    make_spec,
    write_config,
)
from test_plugins.plugin_harness import make_candidate
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.profile_resolver import profile_override
from untaped.settings import get_settings
from untaped.testing import CliInvoker, provider_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_MANAGEMENT = ("config", "profile", "skills", "doctor", "plugins")


def _root(*specs: object, candidates: object = ()) -> object:
    composed = [provider_candidate(spec) for spec in specs]  # type: ignore[arg-type]
    return bootstrap.build_root_app(candidates=[*composed, *candidates])  # type: ignore[misc]


def test_default_root_help_lists_only_management() -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])  # type: ignore[union-attr]
    assert result.exit_code == 0, result.output
    for name in _MANAGEMENT:
        assert name in result.stdout
    for absent in ("workspace", "github", "jira"):
        assert absent not in result.stdout


def test_management_commands_dispatch_through_root(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    github:\n      base_url: https://g\n"
    )
    get_settings.cache_clear()
    root = _root(make_spec("github", settings=GithubProfile))
    for argv in (
        ["config", "get", "github.base_url"],
        ["config", "list"],
        ["profile", "list"],
        ["profile", "current"],
        ["skills", "list"],
        ["doctor"],
        ["plugin", "list"],
    ):
        result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
        assert result.exit_code == 0, (argv, result.output)
    assert profile_override() is None


def test_root_options_work_around_management_commands(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  work:\n    github:\n      base_url: https://w\n")
    get_settings.cache_clear()
    root = _root(make_spec("github", settings=GithubProfile))
    for argv in (
        ["--profile", "work", "config", "get", "github.base_url"],
        ["config", "get", "github.base_url", "--profile", "work"],
    ):
        result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
        assert result.exit_code == 0, (argv, result.output)
        assert "https://w" in result.stdout
    assert profile_override() is None


def test_jira_isolation_end_to_end(_isolated_config: Path) -> None:
    """Invalid Jira settings block neither listing, repair, nor other rows."""
    write_config(
        _isolated_config,
        "profiles:\n  default:\n"
        "    github:\n      base_url: https://g\n"
        "    jira:\n      timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    root = _root(
        make_spec(
            "github",
            settings=GithubProfile,
            doctor_checks=(check("github.auth", detail="github ok"),),
        ),
        make_spec("jira", settings=JiraProfile),
    )
    plugins = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])  # type: ignore[union-attr]
    assert plugins.exit_code == 0, plugins.output
    assert {row["name"] for row in json.loads(plugins.stdout)} == {"github", "jira"}

    doctor = CliInvoker().invoke(root.meta, ["doctor"])  # type: ignore[union-attr]
    assert doctor.exit_code == 1
    assert "timeout" in doctor.stdout
    assert any("github.auth" in line and "✓" in line for line in doctor.stdout.splitlines())

    repair = CliInvoker().invoke(root.meta, ["config", "set", "jira.timeout", "12"])  # type: ignore[union-attr]
    assert repair.exit_code == 0, repair.output

    healed = CliInvoker().invoke(root.meta, ["doctor"])  # type: ignore[union-attr]
    assert healed.exit_code == 0, healed.output


def test_quarantined_provider_lists_and_fails_doctor_only(tmp_path: Path) -> None:
    good = make_candidate(make_spec("good", skills=(asset(tmp_path, "untaped-good"),)))
    bad = make_candidate(make_spec("good"), name="bad")
    root = _root(candidates=(good, bad))

    plugins = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])  # type: ignore[union-attr]
    assert plugins.exit_code == 0, plugins.output
    rows = {row["name"]: row for row in json.loads(plugins.stdout)}
    assert rows["good"]["status"] == "ready"
    assert rows["bad"]["status"] == "quarantined"

    doctor = CliInvoker().invoke(root.meta, ["doctor"])  # type: ignore[union-attr]
    assert doctor.exit_code == 1

    config = CliInvoker().invoke(root.meta, ["config", "list"])  # type: ignore[union-attr]
    assert config.exit_code == 0, config.output


def test_skills_short_selector_through_root(tmp_path: Path) -> None:
    root = _root(make_spec("tools", skills=(asset(tmp_path, "untaped-demo"),)))
    target = tmp_path / "skills"
    result = CliInvoker().invoke(  # type: ignore[union-attr]
        root.meta, ["skills", "install", "demo", "--target-dir", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_state_write_rejected_through_root(_isolated_config: Path) -> None:
    from test_management.support import GithubState

    root = _root(make_spec("github", settings=GithubProfile, state=GithubState))
    result = CliInvoker().invoke(root.meta, ["config", "set", "github.cursor", "x"])  # type: ignore[union-attr]
    assert result.exit_code != 0
    assert "managed by" in result.output
    assert not _isolated_config.exists()


def test_profile_round_trip_through_root() -> None:
    root = _root(make_spec("ghost", settings=ExtProfile))
    assert CliInvoker().invoke(root.meta, ["profile", "create", "work"]).exit_code == 0  # type: ignore[union-attr]
    use = CliInvoker().invoke(root.meta, ["profile", "use", "work"])  # type: ignore[union-attr]
    assert use.exit_code == 0, use.output
    current = CliInvoker().invoke(root.meta, ["profile", "current"])  # type: ignore[union-attr]
    assert current.stdout.strip() == "work"


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "prod", "config", "set", "github.base_url", "https://p"],
        ["config", "set", "github.base_url", "https://p", "--profile", "prod"],
    ],
)
def test_config_set_writes_into_the_root_profile(_isolated_config: Path, argv: list[str]) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  prod: {}\nactive: default\n")
    root = _root(make_spec("github", settings=GithubProfile))
    result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
    assert result.exit_code == 0, result.output
    assert "in profile prod" in result.output
    profiles = read_config_dict(_isolated_config)["profiles"]
    assert profiles["prod"] == {"github": {"base_url": "https://p"}}
    assert profiles["default"] == {}


def test_config_unset_removes_from_the_root_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github: {mode: 'on'}\n  prod:\n    github: {mode: 'on'}\n",
    )
    root = _root(make_spec("github", settings=GithubProfile))
    argv = ["--profile", "prod", "config", "unset", "github.mode"]
    result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
    assert result.exit_code == 0, result.output
    profiles = read_config_dict(_isolated_config)["profiles"]
    assert profiles["prod"] == {}
    assert profiles["default"] == {"github": {"mode": "on"}}


@pytest.mark.parametrize("verb", [["set", "github.base_url", "x"], ["unset", "github.base_url"]])
def test_target_profile_option_is_gone(_isolated_config: Path, verb: list[str]) -> None:
    write_config(_isolated_config, "profiles:\n  default: {}\n  prod: {}\n")
    root = _root(make_spec("github", settings=GithubProfile))
    argv = ["config", *verb, "--target-profile", "prod"]
    result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
    assert result.exit_code == 2
    assert read_config_dict(_isolated_config)["profiles"] == {"default": {}, "prod": {}}


def test_ctrl_c_at_a_config_prompt_exits_130() -> None:
    from untaped.testing import ScriptedPromptBackend, invoke_cli

    result = invoke_cli(
        bootstrap.build_root_app(candidates=()),
        ["config", "set", "ui.theme", "--prompt"],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(interrupt=True),
    )
    assert result.exit_code == 130
