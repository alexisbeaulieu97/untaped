"""The engine asks each provider about the kinds its rules read; what goes wrong is a row."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lint_fakes import (
    Acme,
    Gadget,
    Gizmo,
    Malformed,
    MalformedRule,
    Raising,
    Sprocket,
    Widget,
    tall,
    wide,
)
from untaped.sdk import InstalledPlugin
from untaped.sdk.testing import install
from untaped_lint.api import Finding, Rules, finding, rule
from untaped_lint.plugin import LintPlugin
from untaped_lint.testing import run

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_a_subject_reaches_only_the_rules_reading_its_kind() -> None:
    Acme.forget()
    Gizmo.forget()
    with install(LintPlugin, providers={"acme": [Acme], "gizmo": [Gizmo]}).running():
        report = run([Widget(name="w")])
    assert sorted(each.rule for each in report.findings) == ["acme.tall", "acme.wide"]
    assert (Acme.asked, Gizmo.asked) == (["linttest.widget"], [])
    assert not report.failures


def test_a_kind_no_rule_reads_warns_once_and_asks_nobody() -> None:
    Acme.forget()
    Gizmo.forget()
    with install(LintPlugin, providers={"acme": [Acme], "gizmo": [Gizmo]}).running():
        report = run([Sprocket(name="a"), Sprocket(name="b")])
    assert report.warnings == [
        "no installed rule reads linttest.sprocket records; see `untaped lint rules`"
    ]
    assert (Acme.asked, Gizmo.asked) == ([], [])
    assert not report.findings


def test_a_rule_that_raises_is_one_failure_row_and_the_others_still_count() -> None:
    with install(LintPlugin, providers={"acme": [Raising]}).running():
        report = run([Widget(name="w")])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    (failure,) = report.failures
    assert failure.item == "acme.boom"
    assert failure.details["reason"] == "raised"
    assert "cannot read w" in failure.message


def test_a_malformed_finding_is_counted_and_the_valid_ones_reported() -> None:
    with install(LintPlugin, providers={"acme": [Malformed]}).running():
        report = run([Widget(name="w")])
    assert [(each.rule, each.target) for each in report.findings] == [("acme.wide", "a")]
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "invalid-item")


def test_a_malformed_rule_is_dropped_and_its_provider_s_others_run() -> None:
    """A dotted id is malformed: the engine prefixes the key, so a provider never writes one."""
    with install(LintPlugin, providers={"acme": [MalformedRule], "gizmo": [Gizmo]}).running():
        report = run([Widget(name="w"), Gadget(name="g")])
    assert sorted(each.rule for each in report.findings) == ["acme.wide", "gizmo.default-branch"]
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "invalid-item")
    assert "acme's malformed rule was dropped" in failure.message
    assert "'acme.dotted' is not a bare id" in failure.message


def test_ids_are_prefixed_with_the_providing_plugin() -> None:
    with install(LintPlugin, providers={"acme": [Gizmo]}).running():
        report = run([Gadget(name="g")])
    assert [each.rule for each in report.findings] == ["acme.default-branch"]


def test_a_bare_id_names_its_one_owner() -> None:
    with install(LintPlugin, providers={"acme": [Acme], "gizmo": [Gizmo]}).running():
        report = run([Widget(name="w")], select=["wide"])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    assert not report.warnings


def test_suppressions_and_selections_add_up() -> None:
    """``--ignore`` with a target glob, ``--select`` and an unknown id said once."""
    with install(LintPlugin, providers={"acme": [Acme]}).running():
        report = run(
            [Widget(name="legacy-a"), Widget(name="b")],
            select=["acme.wide", "acme.tall"],
            ignore=["acme.tall", "acme.wide:legacy-*", "nope", "nope"],
        )
    assert [(each.rule, each.target) for each in report.findings] == [("acme.wide", "b")]
    assert report.warnings == ["`nope`: no installed rule; is its plugin installed?"]


class _Twice(Acme):
    """``wide`` listed and aliased, and two rules sharing an id."""

    alias = wide


def test_an_alias_of_a_listed_rule_runs_once() -> None:
    with install(LintPlugin, providers={"acme": [_Twice]}).running():
        report = run([Widget(name="w")])
    assert sorted(each.rule for each in report.findings) == ["acme.tall", "acme.wide"]
    assert not report.failures


class _SameId(Acme):
    RULES = (wide, tall, wide)


def test_a_provider_with_two_rules_of_one_id_is_not_loaded() -> None:
    with install(LintPlugin, providers={"acme": [_SameId]}).running():
        report = run([Widget(name="w")])
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "duplicate-id")
    assert not report.findings


def test_a_narrowed_run_still_reports_a_plugin_nothing_checked() -> None:
    plugin = InstalledPlugin(
        name="ghost", status="ready", distribution="untaped-ghost", version="1"
    )
    with install(LintPlugin).running():
        report = run([plugin], select=["lint.error-case"], candidates=[])
    assert [each.rule for each in report.findings] == ["lint.plugin-not-installed"]


class _MethodAlias(Rules):
    @rule(severity="error")
    def wide(self, widget: Widget) -> Iterator[Finding]:
        """A widget is narrow."""
        yield finding(self.wide, widget.name, "too wide")

    alias = wide


def test_an_alias_of_a_rule_method_runs_once() -> None:
    with install(LintPlugin, providers={"acme": [_MethodAlias]}).running():
        report = run([Widget(name="w")])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    assert not report.failures


def test_locations_keep_only_the_findings_read_there() -> None:
    from untaped_lint.api import DocumentLocation, ResourceLocation
    from untaped_lint.domain.selection import Selection

    def at(location: object) -> Finding:
        return Finding.model_validate(
            {"rule": "acme.tall", "severity": "error", "target": "T/-/x", "message": "m",
             "location": location}
        )  # fmt: skip

    selection = Selection(locations=frozenset({"schedules/400", "a.yml#2"}))
    assert selection.keeps(at(ResourceLocation(id="schedules/400")))
    assert selection.keeps(at(DocumentLocation(path="a.yml", index=2)))
    assert not selection.keeps(at(ResourceLocation(id="schedules/401")))  # same target
    assert not selection.keeps(at(None))
    assert Selection(locations=frozenset({"x"}), unchecked=frozenset({"acme.tall"})).keeps(at(None))
