"""``untaped plugin``: core's group on its own class, and ``plugin list`` in particular.

``plugin list`` reports one record per candidate plugin (``name/status/
distribution/version``) from the composition, in name order. The listing
never touches settings, so invalid plugin values cannot block it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from core_test_support.commands.support import (
    GithubProfile,
    JiraProfile,
    make_spec,
    write_config,
)
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.core.commands.plugins import INSTALL_HINT, List, Schema
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.settings import get_settings
from untaped.sdk.testing import CliInvoker, Install

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _rows(stdout: str) -> list[dict[str, object]]:
    return [dict(item) for item in json.loads(stdout)]


def _listing(candidates: list[PluginCandidate]) -> list[dict[str, object]]:
    """The JSON rows ``untaped plugin list`` lists for ``candidates``."""
    root = bootstrap.build_root_app(candidates=candidates)
    invoked = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert invoked.exit_code == 0, invoked.output
    return _rows(invoked.stdout)


_LOCATED = ("package", "source_dir", "tests_dir", "requires", "fills")


def test_lists_a_ready_provider_with_its_distribution_version() -> None:
    rows = _listing([make_candidate(make_spec("acme"), "acme-dist", distribution_version="1.2.3")])
    assert [{key: row[key] for key in row if key not in _LOCATED} for row in rows] == [
        {"name": "acme", "status": "ready", "distribution": "acme-dist", "version": "1.2.3"}
    ]


def test_a_ready_plugin_says_where_its_code_is() -> None:
    """``untaped.installed_plugin`` records carry what lint's plugin subject reads."""
    [row] = _listing([make_candidate(make_spec("acme"), "acme-dist")])
    assert row["package"] == "core_test_support.commands"
    assert row["source_dir"] == str(Path(__file__).parents[3] / "core_test_support" / "commands")
    assert row["tests_dir"] is None  # no pyproject.toml declaring acme above it


def test_a_ready_plugin_says_what_it_may_import_from() -> None:
    """``requires``: its default requirements, canonical; an extra no owner names adds none."""
    requires = ("Untaped_Git>=1", "jinja2; python_version >= '3'", "rich; extra == 'pretty'")
    [row] = _listing([make_candidate(make_spec("acme"), "acme-dist", requires_dist=requires)])
    assert (row["requires"], row["fills"]) == (["jinja2", "untaped-git"], [])


def test_an_extra_named_after_an_owner_it_fills_adds_to_requires() -> None:
    spec = replace(make_spec("acme"), provides={"rack": lambda: ()})
    requires = ("untaped-rack>=1; extra == 'rack'", "rich; extra == 'pretty'")
    [row] = _listing([make_candidate(spec, "acme-dist", requires_dist=requires)])
    assert (row["requires"], row["fills"]) == (["untaped-rack"], ["rack"])


def test_a_quarantined_blank_distribution_candidate_lists_its_own_version() -> None:
    broken = make_candidate(
        make_spec("acme"), "  ", distribution_version="1.2.3", error=RuntimeError("x")
    )
    assert isinstance(broken.target, str)
    assert _listing([broken]) == [
        {
            "name": "acme",
            "status": "quarantined",
            "distribution": "unknown",
            "version": "1.2.3",
            "package": broken.target.partition(":")[0],  # its entry point names it
            "source_dir": None,
            "tests_dir": None,
            "requires": [],
            "fills": [],
        }
    ]


def test_the_listing_is_in_name_order_across_statuses() -> None:
    broken = make_candidate(make_spec("beta"), "aaa-dist", error=RuntimeError("x"))
    alpha = make_candidate(make_spec("alpha"), "zzz-dist")
    gamma = make_candidate(make_spec("gamma"), "mmm-dist")
    rows = _listing([gamma, broken, alpha])
    assert [(row["name"], row["status"]) for row in rows] == [
        ("alpha", "ready"),
        ("beta", "quarantined"),
        ("gamma", "ready"),
    ]


def test_quarantined_provider_lists_with_entry_point_name() -> None:
    candidate = PluginCandidate(
        distribution="example-dist",
        name="ghost",
        target="example_mod:provider",
        distribution_version="0.1.0",
    )
    assert _listing([candidate]) == [
        {
            "name": "ghost",
            "status": "quarantined",
            "distribution": "example-dist",
            "version": "0.1.0",
            "package": "example_mod",  # from its entry point, though nothing locates its code
            "source_dir": None,
            "tests_dir": None,
            "requires": [],
            "fills": [],
        }
    ]


def test_unresolvable_provider_uses_unknown_sentinels() -> None:
    candidate = PluginCandidate(distribution="unknown", name="mystery", target="nope:missing")
    (row,) = _listing([candidate])
    assert row["name"] == "mystery"
    assert row["version"] == "unknown"


def test_table_headers_name_the_listing_contract() -> None:
    github = make_candidate(make_spec("github", settings=GithubProfile))
    root = bootstrap.build_root_app(candidates=[github])
    invoked = CliInvoker().invoke(root.meta, ["plugin", "list"])
    assert invoked.exit_code == 0, invoked.output
    header = [cell.strip() for cell in invoked.stdout.splitlines()[1].strip("│").split("│")]
    assert header == ["name", "status", "distribution", "version"]
    assert "github" in invoked.stdout


def test_listing_is_not_blocked_by_invalid_settings(_isolated_config: Path) -> None:
    """Broken Jira values still list every plugin."""
    write_config(
        _isolated_config,
        "format_version: 2\njira:\n  timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    listed = _listing(
        [
            make_candidate(make_spec("github", settings=GithubProfile)),
            make_candidate(make_spec("jira", settings=JiraProfile)),
        ]
    )
    assert {row["name"] for row in listed} == {"github", "jira"}


@pytest.mark.parametrize(
    "config",
    [
        pytest.param("untaped:\n  ui:\n    theme: bogus\n", id="unknown-theme"),
        pytest.param("active: nosuch\n", id="unknown-profile"),
        pytest.param("profiles: [unclosed\n", id="unparseable"),
    ],
)
def test_the_table_is_not_blocked_by_a_broken_config(_isolated_config: Path, config: str) -> None:
    """The listing that diagnoses a broken install draws the default theme."""
    write_config(_isolated_config, config)
    get_settings.cache_clear()
    root = bootstrap.build_root_app(candidates=[make_candidate(make_spec("acme"), "acme-dist")])
    invoked = CliInvoker().invoke(root.meta, ["plugin", "list"])
    assert invoked.exit_code == 0, invoked.output
    assert "acme-dist" in invoked.stdout


def test_core_s_commands_run_with_nothing_else_installed(install: Callable[..., Install]) -> None:
    core = install()
    assert core.run(List()).rows == []
    schema = json.loads(core.run(Schema(kind="untaped.installed_plugin")).text)
    assert schema["$id"] == schema["title"] == "untaped.installed_plugin"
    assert core.cli("plugin schema untaped.installed_plugin").text == json.dumps(schema, indent=2)


@pytest.mark.parametrize(
    ("candidates", "expect_hint"),
    [
        pytest.param([], True, id="bare"),
        pytest.param([make_candidate(make_spec("demo"))], False, id="plugin"),
        pytest.param([make_candidate(make_spec("demo"), name="other")], False, id="quarantined"),
    ],
)
def test_plugins_listing_install_hint_on_stderr(
    candidates: list[PluginCandidate], expect_hint: bool
) -> None:
    root = bootstrap.build_root_app(candidates=candidates)
    result = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert result.exit_code == 0
    assert bool(_rows(result.stdout)) is not expect_hint
    if expect_hint:
        # JSON output turns stderr diagnostics into one JSON line, level taken from the prefix.
        assert json.loads(result.stderr) == {
            "level": "hint",
            "kind": "untaped.message",
            "message": INSTALL_HINT,
        }
    else:
        assert "hint:" not in result.stderr
        assert INSTALL_HINT not in result.stderr


def test_bare_plugins_listing_hint_is_a_plain_hint_line_in_text_mode() -> None:
    root = bootstrap.build_root_app(candidates=[])
    result = CliInvoker().invoke(root.meta, ["plugin", "list"])
    assert result.exit_code == 0
    assert result.stderr.strip() == f"hint: {INSTALL_HINT}"
