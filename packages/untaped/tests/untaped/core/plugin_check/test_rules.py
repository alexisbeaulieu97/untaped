"""The convention rules core runs on a real composition, with a real third-party plugin."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from core_test_support.commands.support import make_plugin
from core_test_support.plugin_check.support import Install
from untaped.core.plugin_check.rules import plugin_violations
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import kind_of
from untaped.sdk.testing import PluginCheck, check_plugin, install, plugin_candidate

# A plugin package outside src/untaped, one file per convention family:
# a print (messages, one allowed), a Protocol outside the ports (structure)
# and a domain -> cli import (layering).
_PLUGIN = {
    "demo_plugin/__init__.py": '''
        """Demo plugin."""
        ''',
    "demo_plugin/plugin.py": '''
        """The ``demo`` plugin class."""

        from pydantic import BaseModel, ConfigDict

        from demo_plugin.cli import Nuke
        from untaped.sdk import Plugin


        class DemoSettings(BaseModel):
            """Demo profile settings."""

            model_config = ConfigDict(frozen=True)


        class Demo(Plugin):
            """Demo commands."""

            settings = DemoSettings
            commands = (Nuke,)
        ''',
    "demo_plugin/errors.py": '''
        """Demo errors."""
        ''',
    "demo_plugin/cli.py": '''
        """Demo commands."""

        from untaped.sdk import Command


        class Nuke(Command):
            """Nuke everything."""

            def run(self) -> None:
                print("nuked")
                print("debug")  # untaped: allow print
        ''',
    "demo_plugin/domain/__init__.py": '''
        """Demo domain."""
        ''',
    "demo_plugin/domain/model.py": '''
        """Demo model."""

        from typing import Protocol

        from demo_plugin.cli import Nuke


        class Store(Protocol):
            """A store."""
        ''',
}
_TESTS = {
    "demo_tests/test_demo.py": """
        from demo_plugin._internal import helper
        """,
}


@pytest.fixture
def demo(install: Install) -> list[PluginCandidate]:
    """The demo plugin installed in ``tmp_path/site`` and discovered through its entry point."""
    install({**_PLUGIN, **_TESTS})
    return [
        PluginCandidate(distribution="demo-plugin", name="demo", target="demo_plugin.plugin:Demo")
    ]


# The rules that read only the plugin's files (the print, the layering, the
# test import) are untaped[lint]'s; core finds the rest.
_FOUND = sorted(
    [
        "demo::plugin-name::distribution 'demo-plugin' is not 'untaped-demo'",
        "demo::plugin-name::import package 'demo_plugin' is not 'untaped_demo'",
        "demo_plugin.domain.model.Store::protocol-location",
    ]
)


def test_unknown_plugin_raises() -> None:
    with pytest.raises(LookupError) as raised:
        plugin_violations("no-such-plugin", candidates=[])
    assert str(raised.value) == "no installed plugin named 'no-such-plugin'"


def test_untaped_itself_is_not_an_installed_plugin_to_check() -> None:
    """The root plugin (core) is composed, but the plugin rules don't read it yet.

    Read as an installed plugin, core breaks rules written for one: its
    package is ``untaped``, its commands import the runtime, and its words
    mount at the root. Checking it needs root rules of their own first.
    """
    with pytest.raises(LookupError) as raised:
        plugin_violations("untaped", candidates=[])
    assert str(raised.value) == "no installed plugin named 'untaped'"


class _BrokenRenames(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "missing"}


def test_a_quarantined_plugin_fails_with_why() -> None:
    bad = make_plugin("bad", settings=_BrokenRenames)

    [line] = plugin_violations("bad", candidates=[plugin_candidate(bad)])
    assert line.startswith("bad::quarantined::bad-settings-keys: ")
    assert "missing" in line


def test_a_third_party_plugin_is_checked_from_its_composition(
    demo: list[PluginCandidate],
) -> None:
    """A plugin outside src/untaped is checked like a first-party one."""
    assert plugin_violations("demo", candidates=demo) == _FOUND


def test_a_main_module_is_checked_without_running_it(
    demo: list[PluginCandidate], install: Install
) -> None:
    install({"demo_plugin/__main__.py": 'raise SystemExit("ran __main__")\n'})
    assert plugin_violations("demo", candidates=demo) == _FOUND


def test_a_plugin_check_never_warns_about_a_quarantined_plugin(
    demo: list[PluginCandidate], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = PluginCandidate(distribution="broken", name="broken", target="no_such_mod:SPEC")
    plugin_violations("demo", candidates=[*demo, broken])
    assert "quarantined" not in capsys.readouterr().err  # only the root's composition warns


def test_a_check_keeps_the_composition_and_stays_quiet(
    demo: list[PluginCandidate], capsys: pytest.CaptureFixture[str]
) -> None:
    bootstrap.compose_root(candidates=[])
    before = bootstrap.composition()
    broken = PluginCandidate(distribution="broken", name="broken", target="no_such_mod:SPEC")

    assert check_plugin("demo", candidates=[*demo, broken])

    assert bootstrap.composition() is before
    assert "quarantined" not in capsys.readouterr().err


def test_stability_rules_run_with_the_other_checks(
    demo: list[PluginCandidate], install: Install
) -> None:
    marked = _PLUGIN["demo_plugin/cli.py"].replace(
        '"""Nuke everything."""', '"""Nuke everything. Experimental: may change."""'
    )
    install({"demo_plugin/cli.py": marked})

    assert "demo nuke::hand-typed-mark::docstring" in plugin_violations("demo", candidates=demo)


def test_a_plugin_without_commands_is_found_through_its_settings(install: Install) -> None:
    install(
        {
            "untaped_quiet/__init__.py": '''
                """Quiet plugin: settings, no commands."""
                ''',
            "untaped_quiet/plugin.py": '''
                """The ``quiet`` plugin class."""

                from pydantic import BaseModel, ConfigDict

                from untaped.sdk import Plugin


                class QuietSettings(BaseModel):
                    """Quiet profile settings."""

                    model_config = ConfigDict(frozen=True)


                class Quiet(Plugin):
                    """Quiet plugin: settings, no commands."""

                    settings = QuietSettings
                ''',
            "untaped_quiet/errors.py": '''
                """Quiet errors."""
                ''',
        }
    )
    candidate = PluginCandidate(
        distribution="untaped-quiet", name="quiet", target="untaped_quiet.plugin:Quiet"
    )
    assert plugin_violations("quiet", candidates=[candidate]) == []


def test_a_hyphenated_plugin_is_named_with_underscores_to_import(install: Install) -> None:
    install(
        {
            "acme_tools/__init__.py": '''
                """A hyphenated plugin under the wrong import package."""
                ''',
            "acme_tools/plugin.py": '''
                """The ``acme-tools`` plugin class."""

                from pydantic import BaseModel, ConfigDict

                from untaped.sdk import Plugin


                class AcmeSettings(BaseModel):
                    """Nothing to configure."""

                    model_config = ConfigDict(frozen=True)


                class AcmeTools(Plugin):
                    """A hyphenated plugin under the wrong import package."""

                    settings = AcmeSettings
                ''',
            "acme_tools/errors.py": '''
                """Errors."""
                ''',
        }
    )
    candidate = PluginCandidate(
        distribution="untaped-acme-tools", name="acme-tools", target="acme_tools.plugin:AcmeTools"
    )
    assert plugin_violations("acme-tools", candidates=[candidate]) == [
        "acme-tools::plugin-name::import package 'acme_tools' is not 'untaped_acme_tools'"
    ]


def test_check_plugin_returns_typed_rows_of_the_composed_checks_only(
    demo: list[PluginCandidate],
) -> None:
    """S13's core half: check names and a status, no file conventions, the same record kind."""
    rows = check_plugin("demo", candidates=demo)
    assert all(isinstance(row, PluginCheck) for row in rows)
    assert {(row.check, row.title, row.status) for row in rows} == {
        ("conventions", "plugin-name", "fail"),
        ("conventions", "protocol-location", "fail"),
    }
    with install().running():
        assert kind_of(PluginCheck) == "untaped.plugin_check"
    assert "severity" not in PluginCheck.model_fields


def test_check_plugin_checks_an_uninstalled_plugin_through_candidates() -> None:
    bad = make_plugin("bad", settings=_BrokenRenames)
    [row] = check_plugin("bad", candidates=[plugin_candidate(bad)])
    assert (row.check, row.title, row.status) == ("registration", "bad-settings-keys", "fail")
