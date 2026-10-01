"""``check_conventions`` on a real composition, with a real third-party plugin."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from untaped import bootstrap
from untaped.capabilities.registry import ExternalProvider
from untaped.conventions import capability_violations
from untaped.testing import check_conventions

# A provider package outside src/untaped, one file per convention family:
# an undeclared write (help tree), a print (messages, one allowed), a
# Protocol outside the ports (structure) and a domain -> cli import (layering).
_PLUGIN = {
    "__init__.py": '''
        """Demo capability provider."""

        from __future__ import annotations

        from typing import TYPE_CHECKING

        from pydantic import BaseModel, ConfigDict

        from untaped.sdk import CapabilitySpec

        if TYPE_CHECKING:
            from cyclopts import App


        class DemoSettings(BaseModel):
            """Demo profile settings."""

            model_config = ConfigDict(frozen=True)


        def build_app() -> App:
            from demo_plugin.cli import build

            return build()


        SPEC = CapabilitySpec(
            name="demo",
            app_factory=build_app,
            config_section="demo",
            profile_model=DemoSettings,
        )


        def provider() -> CapabilitySpec:
            return SPEC


        provider.api_requires = ((3, 0), (4, 0))
        ''',
    "errors.py": '''
        """Demo errors."""
        ''',
    "cli.py": '''
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
    "domain/__init__.py": '''
        """Demo domain."""
        ''',
    "domain/model.py": '''
        """Demo model."""

        from typing import Protocol

        from demo_plugin.cli import build


        class Store(Protocol):
            """A store."""
        ''',
}
_TESTS = {
    "test_demo.py": """
        from demo_plugin._internal import helper
        """,
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ExternalProvider]]:
    """The demo plugin installed in ``tmp_path/site`` and discovered as an external."""
    _write(tmp_path / "site" / "demo_plugin", _PLUGIN)
    _write(tmp_path / "demo_tests", _TESTS)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    yield [ExternalProvider(distribution="demo-plugin", name="demo", target="demo_plugin:provider")]
    for module in [name for name in sys.modules if name.split(".")[0] == "demo_plugin"]:
        del sys.modules[module]
    bootstrap._clear_for_tests()  # forget the composition that registered ``demo``


_FOUND = [
    "demo nuke::undeclared-write::--yes",
    "demo_plugin.domain.model.Store::protocol-location",
    "demo_plugin/cli.py::print::print()",
    "demo_plugin/domain/model.py::layer::domain -> demo_plugin.cli",
]
_PRIVATE_IMPORT = "demo_tests/test_demo.py::private-test-import::demo_plugin._internal:_internal"


def test_unknown_capability_raises() -> None:
    with pytest.raises(LookupError) as raised:
        check_conventions("no-such-capability")
    assert str(raised.value) == "no installed capability named 'no-such-capability'"


def test_test_imports_are_checked_only_with_a_tests_dir(
    demo: list[ExternalProvider], tmp_path: Path
) -> None:
    found = capability_violations("demo", tests_dir=tmp_path / "demo_tests", externals=demo)
    assert found == sorted([*_FOUND, _PRIVATE_IMPORT])


def test_an_external_capability_fails_with_every_violation_in_its_own_files(
    demo: list[ExternalProvider],
) -> None:
    """A plugin outside src/untaped, discovered as an external, is checked from its own files."""
    with pytest.raises(AssertionError) as raised:
        check_conventions("demo", externals=demo)
    assert str(raised.value) == "convention violations:\n" + "\n".join(
        f"  {line}" for line in _FOUND
    )


def test_a_quarantined_provider_is_warned_about_once(
    demo: list[ExternalProvider], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = ExternalProvider(distribution="broken", name="broken", target="no_such_mod:provider")
    capability_violations("demo", externals=[*demo, broken])
    assert capsys.readouterr().err.count("quarantined") == 1


def test_an_app_factory_outside_any_package_names_the_capability(
    demo: list[ExternalProvider], tmp_path: Path
) -> None:
    init = tmp_path / "site" / "demo_plugin" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    partial = text.replace("app_factory=build_app", "app_factory=functools.partial(build_app)")
    init.write_text(
        partial.replace("import TYPE_CHECKING", "import TYPE_CHECKING\nimport functools"),
        encoding="utf-8",
    )
    with pytest.raises(LookupError) as raised:
        check_conventions("demo", externals=demo)
    assert str(raised.value) == "capability 'demo': its app factory is not defined in a package"
