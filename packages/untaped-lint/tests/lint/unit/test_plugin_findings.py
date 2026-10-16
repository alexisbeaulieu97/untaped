"""Conventions' findings for a broken third-party plugin: core's rows and the file rules, mapped."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from lint.support import Install
from untaped.sdk import Plugin
from untaped.testing import PluginCandidate, PluginCheckRow, compose_with
from untaped_lint.providers import conventions
from untaped_lint.providers.conventions import Conventions, file_lines
from untaped_lint.records import FileLocation
from untaped_lint.testing import run, subject

pytestmark = pytest.mark.usefixtures("fresh_composition")

# A print (a file rule), a Protocol outside the ports (core), a domain -> cli
# import (a file rule) and a write without --yes (core).
_PLUGIN = {
    "demo_plugin/__init__.py": '''
        """Demo plugin."""

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


        SPEC = PluginSpec(name="demo", app_factory=build_app, settings=DemoSettings)
        ''',
    "demo_plugin/errors.py": '"""Demo errors."""\n',
    "demo_plugin/cli.py": '''
        """Demo commands."""

        from __future__ import annotations

        from cyclopts import App

        from untaped.sdk import YesOption, create_app


        def nuke(*, yes: YesOption = False) -> None:
            """Nuke everything."""
            print("nuked")


        def build() -> App:
            app = create_app(name="demo", help="Demo commands.")
            app.command(nuke, name="nuke")
            return app
        ''',
    "demo_plugin/domain/__init__.py": '"""Demo domain."""\n',
    "demo_plugin/domain/model.py": '''
        """Demo model."""

        from typing import Protocol

        from demo_plugin.cli import build


        class Store(Protocol):
            """A store."""
        ''',
}
_DEMO = [PluginCandidate(distribution="demo-plugin", name="demo", target="demo_plugin:SPEC")]


def _plugin(site: Path) -> Plugin:
    return Plugin(
        name="demo",
        status="ready",
        distribution="demo-plugin",
        version="0.1.0",
        package="demo_plugin",
        source_dir=site / "demo_plugin",
    )


def test_core_rows_and_file_lines_become_findings(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = install(_PLUGIN)
    monkeypatch.chdir(site)
    found = Conventions().findings(_plugin(site), candidates=_DEMO)
    by_rule = {finding.rule: finding for finding in found}
    assert {"plugin-name", "protocol-location", "undeclared-write", "print", "layer"} <= set(
        by_rule
    )
    assert {finding.severity for finding in found} == {"error"}
    assert {finding.target for finding in found} == {"demo"}
    # A file rule's line becomes a location relative to the cwd, its detail the message.
    assert by_rule["print"].location == FileLocation(path="demo_plugin/cli.py")
    assert by_rule["print"].message == "print()"
    assert by_rule["layer"].location == FileLocation(path="demo_plugin/domain/model.py")


def test_a_file_outside_the_cwd_is_shown_absolute(install: Install, tmp_path: Path) -> None:
    site = install(_PLUGIN)
    found = Conventions().findings(_plugin(site), candidates=_DEMO)
    [printed] = [finding for finding in found if finding.rule == "print"]
    assert isinstance(printed.location, FileLocation)
    assert printed.location.path == (site / "demo_plugin" / "cli.py").as_posix()


def test_a_plugin_not_installed_is_one_finding(install: Install) -> None:
    site = install(_PLUGIN)
    [finding] = Conventions().findings(_plugin(site), candidates=[])
    assert (finding.rule, finding.target) == ("plugin-not-installed", "demo")
    assert finding.hint is not None


def test_an_unreadable_file_is_one_line_and_stops_the_file_rules(install: Install) -> None:
    site = install({**_PLUGIN})
    (site / "demo_plugin" / "broken.py").write_bytes(b"\xff\xfe not utf-8")
    lines = file_lines("demo_plugin", site / "demo_plugin", plugin="demo")
    [line] = lines
    assert line.startswith("demo_plugin/broken.py::unreadable-file::")


def test_an_unreadable_file_is_one_finding_from_the_whole_run(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = install(_PLUGIN)
    monkeypatch.chdir(site)
    (site / "demo_plugin" / "broken.py").write_bytes(b"\xff\xfe not utf-8")
    found = Conventions().findings(_plugin(site), candidates=_DEMO)
    [finding] = found
    assert (finding.rule, finding.message) == ("unreadable-file", "is not UTF-8")
    assert finding.location == FileLocation(path="demo_plugin/broken.py")


@pytest.mark.parametrize("status", ["ready", "quarantined"])
def test_a_plugin_composition_refuses_is_the_quarantined_finding(
    install: Install, status: Literal["ready", "quarantined"]
) -> None:
    """Read from what the check found now, whatever the (maybe stale) record says."""
    install(_PLUGIN)
    broken = PluginCandidate(distribution="demo-plugin", name="demo", target="demo_plugin:NOPE")
    record = Plugin(name="demo", status=status, distribution="demo-plugin", version="0.1.0")
    [finding] = Conventions().findings(record, candidates=[broken])
    assert (finding.rule, finding.severity) == ("quarantined", "error")


def test_a_stale_quarantined_record_of_a_plugin_that_composes_keeps_its_rules(
    install: Install,
) -> None:
    site = install(_PLUGIN)
    record = _plugin(site).model_copy(update={"status": "quarantined"})
    rules = {finding.rule for finding in Conventions().findings(record, candidates=_DEMO)}
    assert "quarantined" not in rules
    assert {"plugin-name", "undeclared-write"} <= rules


def _row(check: str, title: str, status: str, detail: str) -> PluginCheckRow:
    return PluginCheckRow(plugin="demo", check=check, title=title, status=status, detail=detail)


def test_each_row_status_maps_to_a_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _row("conventions", "conventions", "pass", "no violations"),
        _row("live", "rack.item_source.items", "pass", "1 item"),
        _row("live", "rack.item_source.items", "fail", "NotImplementedError"),
        _row("schema", "rack.item_source", "warn", "no recorded owner schema hash"),
    ]
    monkeypatch.setattr(conventions, "check_plugin", lambda name, candidates=None: rows)
    plugin = Plugin(name="demo", status="ready", distribution="demo-plugin", version="0.1.0")
    found = Conventions().findings(plugin)
    assert [(each.rule, each.severity, each.message) for each in found] == [
        ("live", "error", "rack.item_source.items: NotImplementedError"),
        ("schema", "warning", "rack.item_source: no recorded owner schema hash"),
    ]


def test_a_narrowed_run_still_reports_an_unreadable_file(install: Install) -> None:
    site = install(_PLUGIN)
    (site / "demo_plugin" / "broken.py").write_bytes(b"\xff\xfe not utf-8")
    with compose_with("lint"):
        report = run([subject(_plugin(site))], select=["lint.print"], candidates=_DEMO)
    assert [each.rule for each in report.findings] == ["lint.unreadable-file"]


_API = '''
    """Demo's Python API."""

    import warnings
    from typing import Annotated, overload

    from untaped.sdk import deprecated

    __all__ = ["drifted", "emit", "pick"]


    @deprecated(removed_in=3)
    @warnings.deprecated("drifted is deprecated")
    def drifted() -> None:
        """Its literal is not its mark's sentence."""


    def emit(kind: Annotated[str, deprecated(removed_in=3)] = "") -> None:
        """A marked parameter nothing warns about."""


    @overload
    @deprecated(removed_in=3)
    def pick(x: int) -> int: ...
    @overload
    def pick(x: str) -> str: ...
    def pick(x):
        """A mark on an overload."""
        return x
    '''


def test_the_deprecation_rules_run_as_lint_ids(install: Install) -> None:
    site = install({**_PLUGIN, "demo_plugin/api.py": _API})
    rules = ["lint.deprecation-text", "lint.unwarned-parameter", "lint.mark-on-overload"]
    with compose_with("lint"):
        report = run([subject(_plugin(site))], select=rules, candidates=_DEMO)
    assert sorted(each.rule for each in report.findings) == [
        "lint.deprecation-text",
        "lint.mark-on-overload",
        "lint.unwarned-parameter",
        "lint.unwarned-parameter",
    ]
