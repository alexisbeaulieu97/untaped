"""Integration tests for the Wave 1.4 root management surface.

The unified root mounts exactly the five management commands (no capability
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
from untaped import bootstrap
from untaped.profile_resolver import profile_override
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

_MANAGEMENT = ("config", "profile", "skills", "doctor", "capabilities")


def _root(*specs: object, externals: object = ()) -> object:
    return bootstrap.build_root_app(builtins=tuple(specs), externals=tuple(externals))  # type: ignore[arg-type]


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
    root = _root(make_spec("github", profile_model=GithubProfile))
    for argv in (
        ["config", "get", "github.base_url"],
        ["config", "list"],
        ["profile", "list"],
        ["profile", "current"],
        ["skills", "list"],
        ["doctor"],
        ["capabilities"],
    ):
        result = CliInvoker().invoke(root.meta, argv)  # type: ignore[union-attr]
        assert result.exit_code == 0, (argv, result.output)
    assert bootstrap.current_capability() is None
    assert profile_override() is None


def test_root_options_work_around_management_commands(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  work:\n    github:\n      base_url: https://w\n")
    get_settings.cache_clear()
    root = _root(make_spec("github", profile_model=GithubProfile))
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
            profile_model=GithubProfile,
            doctor_checks=(check("github.auth", detail="github ok"),),
        ),
        make_spec("jira", profile_model=JiraProfile),
    )
    capabilities = CliInvoker().invoke(root.meta, ["capabilities", "--format", "json"])  # type: ignore[union-attr]
    assert capabilities.exit_code == 0, capabilities.output
    assert {row["name"] for row in json.loads(capabilities.stdout)} == {"github", "jira"}

    doctor = CliInvoker().invoke(root.meta, ["doctor"])  # type: ignore[union-attr]
    assert doctor.exit_code == 1
    assert "timeout" in doctor.stdout
    assert "github ok" in doctor.stdout

    repair = CliInvoker().invoke(root.meta, ["config", "set", "jira.timeout", "12"])  # type: ignore[union-attr]
    assert repair.exit_code == 0, repair.output

    healed = CliInvoker().invoke(root.meta, ["doctor"])  # type: ignore[union-attr]
    assert healed.exit_code == 0, healed.output


def test_quarantined_external_lists_and_fails_doctor_only(tmp_path: Path) -> None:
    from untaped.capabilities.registry import CapabilitySpec, ExternalProvider

    calls: list[str] = []

    class _Provider:
        api_requires = (1.0, 2.0)

        def __call__(self) -> CapabilitySpec:
            calls.append("good")
            return make_spec("good", skills=(asset(tmp_path, "untaped-good"),))

    good = ExternalProvider(distribution="example-dist", name="good", target=_Provider())

    class _BadProvider:
        api_requires = (1.0, 2.0)

        def __call__(self) -> CapabilitySpec:
            return make_spec("good")

    bad = ExternalProvider(distribution="example-dist", name="bad", target=_BadProvider())
    root = _root(externals=(good, bad))

    capabilities = CliInvoker().invoke(root.meta, ["capabilities", "--format", "json"])  # type: ignore[union-attr]
    assert capabilities.exit_code == 0, capabilities.output
    rows = {row["name"]: row for row in json.loads(capabilities.stdout)}
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

    root = _root(make_spec("github", profile_model=GithubProfile, state_model=GithubState))
    result = CliInvoker().invoke(root.meta, ["config", "set", "github.cursor", "x"])  # type: ignore[union-attr]
    assert result.exit_code != 0
    assert "managed by" in result.output
    assert not _isolated_config.exists()


def test_profile_round_trip_through_root() -> None:
    root = _root(make_spec("ghost", profile_model=ExtProfile))
    assert CliInvoker().invoke(root.meta, ["profile", "create", "work"]).exit_code == 0  # type: ignore[union-attr]
    use = CliInvoker().invoke(root.meta, ["profile", "use", "work"])  # type: ignore[union-attr]
    assert use.exit_code == 0, use.output
    current = CliInvoker().invoke(root.meta, ["profile", "current"])  # type: ignore[union-attr]
    assert current.stdout.strip() == "work"
