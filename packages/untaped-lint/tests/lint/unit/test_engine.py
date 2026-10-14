"""The engine asks each provider about the kinds its rules read; what goes wrong is a row."""

from __future__ import annotations

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
)
from untaped.testing import compose_with
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
