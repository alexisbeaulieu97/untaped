"""The engine asks each provider about the kinds its rules read; what goes wrong is a row."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lint.fakes import (
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
from untaped.sdk import Plugin
from untaped.testing import compose_with
from untaped_lint.api import Finding, Rules, finding, rule
from untaped_lint.records import Subject
from untaped_lint.testing import run, subject

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_a_subject_reaches_only_the_rules_reading_its_kind() -> None:
    acme, gizmo = Acme(), Gizmo()
    with compose_with("lint", provides={"acme": [acme], "gizmo": [gizmo]}):
        report = run([subject(Widget(name="w"))])
    assert sorted(each.rule for each in report.findings) == ["acme.tall", "acme.wide"]
    assert (acme.asked, gizmo.asked) == (["linttest.widget"], [])
    assert not report.failures


def test_a_kind_no_rule_reads_warns_once_and_asks_nobody() -> None:
    acme, gizmo = Acme(), Gizmo()
    with compose_with("lint", provides={"acme": [acme], "gizmo": [gizmo]}):
        report = run([subject(Sprocket(name="a")), subject(Sprocket(name="b"))])
    assert report.warnings == [
        "no installed rule reads linttest.sprocket records; see `untaped lint rules`"
    ]
    assert (acme.asked, gizmo.asked) == ([], [])
    assert not report.findings


def test_a_rule_that_raises_is_one_failure_row_and_the_others_still_count() -> None:
    with compose_with("lint", provides={"acme": [Raising()]}):
        report = run([subject(Widget(name="w"))])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    (failure,) = report.failures
    assert failure.item == "acme.boom"
    assert failure.details["reason"] == "raised"
    assert "cannot read w" in failure.message


def test_a_malformed_finding_is_counted_and_the_valid_ones_reported() -> None:
    with compose_with("lint", provides={"acme": [Malformed()]}):
        report = run([subject(Widget(name="w"))])
    assert [(each.rule, each.target) for each in report.findings] == [("acme.wide", "a")]
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "invalid-item")


def test_a_malformed_rule_skips_its_provider_with_one_row() -> None:
    """A dotted id is malformed: the engine prefixes the key, so a provider never writes one."""
    with compose_with("lint", provides={"acme": [MalformedRule()], "gizmo": [Gizmo()]}):
        report = run([subject(Widget(name="w")), subject(Gadget(name="g"))])
    assert [each.rule for each in report.findings] == ["gizmo.default-branch"]
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "invalid-item")
    assert "row 1: id" in failure.message


def test_ids_are_prefixed_with_the_providing_plugin() -> None:
    with compose_with("lint", provides={"acme": [Gizmo()]}):
        report = run([subject(Gadget(name="g"))])
    assert [each.rule for each in report.findings] == ["acme.default-branch"]


def test_an_id_a_rule_was_renamed_from_is_honoured_with_a_warning() -> None:
    with compose_with("lint", provides={"acme": [Gizmo()]}):
        report = run([subject(Gadget(name="g"))], ignore=["acme.old"])
    assert not report.findings
    assert report.warnings == ["`acme.old` is now `acme.default-branch`"]


def test_a_bare_id_names_its_one_owner() -> None:
    with compose_with("lint", provides={"acme": [Acme()], "gizmo": [Gizmo()]}):
        report = run([subject(Widget(name="w"))], select=["wide"])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    assert not report.warnings


def test_suppressions_and_selections_add_up() -> None:
    """``--ignore`` with a target glob, ``--select`` and an unknown id said once."""
    with compose_with("lint", provides={"acme": [Acme()]}):
        report = run(
            [subject(Widget(name="legacy-a")), subject(Widget(name="b"))],
            select=["acme.wide", "acme.tall"],
            ignore=["acme.tall", "acme.wide:legacy-*", "nope", "nope"],
        )
    assert [(each.rule, each.target) for each in report.findings] == [("acme.wide", "b")]
    assert report.warnings == ["`nope`: no installed rule; is its plugin installed?"]


class _Twice(Acme):
    """``wide`` listed and aliased, and two rules sharing an id."""

    alias = wide


def test_an_alias_of_a_listed_rule_runs_once() -> None:
    with compose_with("lint", provides={"acme": [_Twice()]}):
        report = run([subject(Widget(name="w"))])
    assert sorted(each.rule for each in report.findings) == ["acme.tall", "acme.wide"]
    assert not report.failures


class _SameId(Acme):
    RULES = (wide, tall, wide)


def test_a_provider_with_two_rules_of_one_id_is_not_loaded() -> None:
    with compose_with("lint", provides={"acme": [_SameId()]}):
        report = run([subject(Widget(name="w"))])
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("acme", "duplicate-id")
    assert not report.findings


def test_an_invalid_plugin_record_is_a_failure_row_not_a_crash() -> None:
    with compose_with("lint"):
        report = run([Subject(kind="untaped.plugin", record={"name": "git"})])
    (failure,) = report.failures
    assert (failure.item, failure.details["reason"]) == ("git", "invalid-record")
    assert failure.category == "invalid"


def test_a_narrowed_run_still_reports_a_plugin_nothing_checked() -> None:
    plugin = Plugin(name="ghost", status="ready", distribution="untaped-ghost", version="1")
    with compose_with("lint"):
        report = run([subject(plugin)], select=["lint.error-case"], candidates=[])
    assert [each.rule for each in report.findings] == ["lint.plugin-not-installed"]


class _MethodAlias(Rules):
    @rule("wide", reads=Widget, severity="error", summary="A widget is narrow.")
    def wide(self, widget: Widget) -> Iterator[Finding]:
        yield finding(self.wide, widget.name, "too wide")

    alias = wide


def test_an_alias_of_a_rule_method_runs_once() -> None:
    with compose_with("lint", provides={"acme": [_MethodAlias()]}):
        report = run([subject(Widget(name="w"))])
    assert [each.rule for each in report.findings] == ["acme.wide"]
    assert not report.failures
