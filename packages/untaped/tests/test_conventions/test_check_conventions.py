"""``check_conventions`` on a real composition, with a real third-party plugin."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from cyclopts import App
from pydantic import BaseModel

from test_conventions.support import Install
from untaped.conventions import plugin_violations
from untaped.plugins.registry import PluginSpec, ProviderCandidate
from untaped.testing import check_conventions, provider_candidate

# A provider package outside src/untaped, one file per convention family:
# an undeclared write (help tree), a print (messages, one allowed), a
# Protocol outside the ports (structure) and a domain -> cli import (layering).
_PLUGIN = {
    "demo_plugin/__init__.py": '''
        """Demo plugin provider."""

        from __future__ import annotations

        from typing import TYPE_CHECKING

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import PluginSpec

        if TYPE_CHECKING:
            from cyclopts import App


        class DemoSettings(BaseModel):
            """Demo profile settings."""

            model_config = ConfigDict(frozen=True)


        def build_app() -> App:
            from demo_plugin.cli import build

            return build()


        SPEC = PluginSpec(
            name="demo",
            app_factory=build_app,
            settings=DemoSettings,
        )


        def provider() -> PluginSpec:
            return SPEC

        ''',
    "demo_plugin/errors.py": '''
        """Demo errors."""
        ''',
    "demo_plugin/cli.py": '''
        """Demo commands."""

        from __future__ import annotations

        from cyclopts import App

        from untaped.sdk import YesOption, create_app


        def nuke(*, yes: YesOption = False) -> None:
            """Nuke everything."""
            print("nuked")
            print("debug")  # untaped: allow print


        def build() -> App:
            app = create_app(name="demo", help="Demo commands.")
            app.command(nuke, name="nuke")
            return app
        ''',
    "demo_plugin/domain/__init__.py": '''
        """Demo domain."""
        ''',
    "demo_plugin/domain/model.py": '''
        """Demo model."""

        from typing import Protocol

        from demo_plugin.cli import build


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
def demo(install: Install) -> list[ProviderCandidate]:
    """The demo plugin installed in ``tmp_path/site`` and discovered through its entry point."""
    install({**_PLUGIN, **_TESTS})
    return [
        ProviderCandidate(distribution="demo-plugin", name="demo", target="demo_plugin:provider")
    ]


_FOUND = [
    "demo nuke::undeclared-write::--yes",
    "demo::plugin-name::distribution 'demo-plugin' is not 'untaped-demo'",
    "demo::plugin-name::import package 'demo_plugin' is not 'untaped_demo'",
    "demo_plugin.domain.model.Store::protocol-location",
    "demo_plugin/cli.py::print::print()",
    "demo_plugin/domain/model.py::layer::domain -> demo_plugin.cli",
]
_PRIVATE_IMPORT = "demo_tests/test_demo.py::private-test-import::demo_plugin._internal:_internal"


def test_unknown_plugin_raises() -> None:
    with pytest.raises(LookupError) as raised:
        check_conventions("no-such-plugin", candidates=[])
    assert str(raised.value) == "no installed plugin named 'no-such-plugin'"


class _BrokenRenames(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "missing"}


def test_a_quarantined_plugin_fails_with_why() -> None:
    spec = PluginSpec(name="bad", app_factory=App, settings=_BrokenRenames)

    with pytest.raises(AssertionError) as raised:
        check_conventions("bad", candidates=[provider_candidate(spec)])

    [line] = str(raised.value).splitlines()[1:]
    assert line.startswith("  bad::quarantined::bad-settings-keys: ")
    assert "missing" in line


def test_test_imports_are_checked_only_with_a_tests_dir(
    demo: list[ProviderCandidate], tmp_path: Path
) -> None:
    found = plugin_violations("demo", tests_dir=tmp_path / "site" / "demo_tests", candidates=demo)
    assert found == sorted([*_FOUND, _PRIVATE_IMPORT])


def test_a_third_party_plugin_fails_with_every_violation_in_its_own_files(
    demo: list[ProviderCandidate],
) -> None:
    """A plugin outside src/untaped is checked from its own files."""
    with pytest.raises(AssertionError) as raised:
        check_conventions("demo", candidates=demo)
    assert str(raised.value) == "convention violations:\n" + "\n".join(
        f"  {line}" for line in _FOUND
    )


def test_a_main_module_is_checked_without_running_it(
    demo: list[ProviderCandidate], install: Install
) -> None:
    install({"demo_plugin/__main__.py": 'raise SystemExit("ran __main__")\n'})
    assert plugin_violations("demo", candidates=demo) == _FOUND


def test_a_quarantined_provider_is_warned_about_once(
    demo: list[ProviderCandidate], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = ProviderCandidate(distribution="broken", name="broken", target="no_such_mod:provider")
    plugin_violations("demo", candidates=[*demo, broken])
    assert capsys.readouterr().err.count("quarantined") == 1


def test_an_app_factory_outside_any_package_names_the_plugin(
    demo: list[ProviderCandidate], tmp_path: Path
) -> None:
    init = tmp_path / "site" / "demo_plugin" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    partial = text.replace("app_factory=build_app", "app_factory=functools.partial(build_app)")
    init.write_text(
        partial.replace("import TYPE_CHECKING", "import TYPE_CHECKING\nimport functools"),
        encoding="utf-8",
    )
    with pytest.raises(LookupError) as raised:
        check_conventions("demo", candidates=demo)
    assert str(raised.value) == (
        "plugin 'demo': its app factory or settings are not defined in a package"
    )


def test_stability_rules_run_with_the_other_checks(
    demo: list[ProviderCandidate], install: Install
) -> None:
    marked = (
        _PLUGIN["demo_plugin/cli.py"]
        .replace(
            'create_app(name="demo", help="Demo commands.")',
            'create_app(name="demo", help="Demo commands.", stability=experimental)',
        )
        .replace("import YesOption, create_app", "import YesOption, create_app, experimental")
    )
    install({"demo_plugin/cli.py": marked})

    assert "demo::mark-on-spec::demo" in plugin_violations("demo", candidates=demo)


def test_a_plugin_without_commands_is_found_through_its_settings(install: Install) -> None:
    install(
        {
            "untaped_quiet/__init__.py": '''
                """Quiet plugin provider: settings, no commands."""

                from pydantic import BaseModel, ConfigDict

                from untaped.sdk import PluginSpec


                class QuietSettings(BaseModel):
                    """Quiet profile settings."""

                    model_config = ConfigDict(frozen=True)


                def provider() -> PluginSpec:
                    return PluginSpec(name="quiet", settings=QuietSettings)
                ''',
            "untaped_quiet/errors.py": '''
                """Quiet errors."""
                ''',
        }
    )
    candidate = ProviderCandidate(
        distribution="untaped-quiet", name="quiet", target="untaped_quiet:provider"
    )
    assert plugin_violations("quiet", candidates=[candidate]) == []
