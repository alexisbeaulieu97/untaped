"""``untaped plugin``: core's group on its own class, and ``plugin list`` in particular.

``plugin list`` reports one record per composed plugin (``name/status/
distribution/version``), core's ``untaped`` row (marked ``root``) among them,
then each candidate, in name order. The listing never touches settings, so
invalid plugin values cannot block it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import pytest

from core_test_support.commands.support import (
    GithubProfile,
    JiraProfile,
    make_plugin,
    write_config,
)
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.core.commands.plugins import INSTALL_HINT, List, Schema
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.settings import get_settings
from untaped.sdk.contracts import Contract, Provider
from untaped.sdk.testing import CliInvoker, Install

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _rows(stdout: str) -> list[dict[str, object]]:
    return [dict(item) for item in json.loads(stdout)]


def _listing(candidates: list[PluginCandidate]) -> list[dict[str, object]]:
    """The JSON rows ``untaped plugin list`` lists for ``candidates``: core's row aside."""
    return [row for row in _every_row(candidates) if not row["root"]]


def _every_row(candidates: list[PluginCandidate]) -> list[dict[str, object]]:
    root = bootstrap.build_root_app(candidates=candidates)
    invoked = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert invoked.exit_code == 0, invoked.output
    return _rows(invoked.stdout)


def test_core_is_listed_as_the_root_with_untaped_s_version() -> None:
    """Core's row is the composition's like any plugin's, marked ``root``, in name order."""
    rows = _every_row([make_candidate(make_plugin("zeta"), "zeta-dist")])
    assert [(row["name"], row["root"]) for row in rows] == [("untaped", True), ("zeta", False)]
    core = rows[0]
    assert (core["status"], core["distribution"]) == ("ready", "untaped")
    assert core["version"] == metadata.version("untaped")  # read at listing, not composing
    assert core["package"] == "untaped.core"  # what lint's import rules reach: untaped.core.api


_LOCATED = ("package", "source_dir", "tests_dir", "requires", "fills", "root")


def test_lists_a_ready_provider_with_its_distribution_version() -> None:
    rows = _listing(
        [make_candidate(make_plugin("acme"), "acme-dist", distribution_version="1.2.3")]
    )
    assert [{key: row[key] for key in row if key not in _LOCATED} for row in rows] == [
        {"name": "acme", "status": "ready", "distribution": "acme-dist", "version": "1.2.3"}
    ]


def test_a_ready_plugin_says_where_its_code_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``untaped.installed_plugin`` records carry what lint's plugin subject reads."""
    package = tmp_path / "site" / "untaped_located"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Located plugin."""\n', encoding="utf-8")
    (package / "plugin.py").write_text(_LOCATED_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(str(package.parent))
    monkeypatch.delitem(sys.modules, "untaped_located", raising=False)
    target = "untaped_located.plugin:Located"
    [row] = _listing([PluginCandidate(distribution="d", name="located", target=target)])
    assert row["package"] == "untaped_located"  # its class's package
    assert row["source_dir"] == str(package.resolve())
    assert row["tests_dir"] is None  # no pyproject.toml declaring it above it


_LOCATED_PLUGIN = """\
\"\"\"The located plugin's class.\"\"\"

from pydantic import BaseModel

from untaped.sdk import Plugin


class LocatedSettings(BaseModel):
    \"\"\"Nothing to set.\"\"\"


class Located(Plugin):
    \"\"\"A plugin with code on disk.\"\"\"

    settings = LocatedSettings
"""


def test_a_ready_plugin_says_what_it_may_import_from() -> None:
    """``requires``: its default requirements, canonical; an extra adds none."""
    requires = ("Untaped_Git>=1", "jinja2; python_version >= '3'", "rich; extra == 'pretty'")
    [row] = _listing([make_candidate(make_plugin("acme"), "acme-dist", requires_dist=requires)])
    assert (row["requires"], row["fills"]) == (["jinja2", "untaped-git"], [])


class Items(Contract):
    """Where items come from."""

    def items(self) -> list[str]:
        raise NotImplementedError


class AcmeItems(Items, Provider):
    """Items from acme."""

    def items(self) -> list[str]:
        return []


def test_a_plugin_lists_the_owner_it_fills_among_its_requires() -> None:
    rack = make_candidate(make_plugin("rack", offers=(Items,)), "untaped-rack")
    acme = make_plugin("acme", fills=(AcmeItems,))
    requires = ("untaped-rack>=1", "untaped-rack>=1; extra == 'rack'", "rich; extra == 'pretty'")
    rows = _listing([rack, make_candidate(acme, "acme-dist", requires_dist=requires)])
    [row] = [row for row in rows if row["name"] == "acme"]
    assert (row["requires"], row["fills"]) == (["untaped-rack"], ["rack"])


def test_a_quarantined_blank_distribution_candidate_lists_its_own_version() -> None:
    broken = make_candidate(
        make_plugin("acme"), "  ", distribution_version="1.2.3", error=RuntimeError("x")
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
            "root": False,
        }
    ]


def test_the_listing_is_in_name_order_across_statuses() -> None:
    broken = make_candidate(make_plugin("beta"), "aaa-dist", error=RuntimeError("x"))
    alpha = make_candidate(make_plugin("alpha"), "zzz-dist")
    gamma = make_candidate(make_plugin("gamma"), "mmm-dist")
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
            "root": False,
        }
    ]


def test_unresolvable_provider_uses_unknown_sentinels() -> None:
    candidate = PluginCandidate(distribution="unknown", name="mystery", target="nope:missing")
    (row,) = _listing([candidate])
    assert row["name"] == "mystery"
    assert row["version"] == "unknown"


def test_table_headers_name_the_listing_contract() -> None:
    github = make_candidate(make_plugin("github", settings=GithubProfile))
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
            make_candidate(make_plugin("github", settings=GithubProfile)),
            make_candidate(make_plugin("jira", settings=JiraProfile)),
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
    root = bootstrap.build_root_app(candidates=[make_candidate(make_plugin("acme"), "acme-dist")])
    invoked = CliInvoker().invoke(root.meta, ["plugin", "list"])
    assert invoked.exit_code == 0, invoked.output
    assert "acme-dist" in invoked.stdout


def test_core_s_commands_run_with_nothing_else_installed(install: Callable[..., Install]) -> None:
    core = install()
    assert [(row.name, row.root) for row in core.run(List()).rows] == [("untaped", True)]
    schema = json.loads(core.run(Schema(kind="untaped.installed_plugin")).text)
    assert schema["$id"] == schema["title"] == "untaped.installed_plugin"
    assert core.cli("plugin schema untaped.installed_plugin").text == json.dumps(schema, indent=2)


@pytest.mark.parametrize(
    ("candidates", "expect_hint"),
    [
        pytest.param([], True, id="bare"),
        pytest.param([make_candidate(make_plugin("demo"))], False, id="plugin"),
        pytest.param(
            [make_candidate(make_plugin("demo"), error=RuntimeError("x"))], False, id="quarantined"
        ),
    ],
)
def test_plugins_listing_install_hint_on_stderr(
    candidates: list[PluginCandidate], expect_hint: bool
) -> None:
    root = bootstrap.build_root_app(candidates=candidates)
    result = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert result.exit_code == 0
    assert any(not row["root"] for row in _rows(result.stdout)) is not expect_hint
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
