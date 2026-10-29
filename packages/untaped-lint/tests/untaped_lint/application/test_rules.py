"""``@rule``: the id, summary and model a rule reads come from the function itself."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lint_fake_records import Linttest, Widget
from lint_fakes import default_branch
from untaped.sdk import Record
from untaped.sdk.testing import install
from untaped_lint.api import Finding, Rules, finding, rule, rule_row
from untaped_lint.testing import run_rule


def test_the_id_summary_and_model_are_read_from_the_function() -> None:
    with install(Linttest).running():
        declared = rule_row(default_branch)
    assert (declared.id, declared.reads) == ("default-branch", "linttest.gadget")
    assert declared.severity == "error"
    assert declared.summary == "A gadget names its default branch."


@rule(severity="warning")
def long_summary(widget: Widget) -> Iterator[Finding]:
    """A widget's summary is the docstring's first paragraph,
    read as one line.

    The rest says how the check works.
    """
    yield finding(long_summary, widget.name, "seen")


def test_the_summary_is_the_first_paragraph_on_one_line() -> None:
    assert long_summary.summary == (
        "A widget's summary is the docstring's first paragraph, read as one line."
    )


class _Methods(Rules):
    @rule(severity="error")
    def narrow(self, widget: Widget) -> Iterator[Finding]:
        """A widget is narrow."""
        yield finding(self.narrow, widget.name, "too wide")


def test_a_method_reads_the_parameter_after_self() -> None:
    with install(Linttest).running():
        (declared,) = _Methods().rules()
    assert (declared.id, declared.reads) == ("narrow", "linttest.widget")


def _undocumented(widget: Widget) -> Iterator[Finding]:
    yield from ()


def _unannotated(widget) -> Iterator[Finding]:  # type: ignore[no-untyped-def]
    """Reads nothing it names."""
    yield from ()


def _no_record() -> Iterator[Finding]:
    """Takes nothing."""
    yield from ()


def bad__name(widget: Widget) -> Iterator[Finding]:
    """Its name is no kebab-case id."""
    yield from ()


@pytest.mark.parametrize(
    ("function", "message"),
    [
        (_undocumented, "has no docstring: its first paragraph is the summary"),
        (_unannotated, "annotate 'widget' with the record model it reads"),
        (_no_record, "takes no record: its first parameter is what it reads"),
        (bad__name, "its name is its id"),
    ],
)
def test_a_rule_the_function_cannot_declare_is_a_type_error(function: object, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        rule(severity="error")(function)  # type: ignore[arg-type]


class _Unclaimed(Record):
    """A record no composed plugin claims."""


@rule(severity="error")
def reads_unclaimed(record: _Unclaimed) -> Iterator[Finding]:
    """Reads a model with no kind."""
    yield from ()


def test_a_rule_reading_a_model_without_a_kind_has_no_row() -> None:
    with pytest.raises(TypeError, match="_Unclaimed declares no kind"):
        rule_row(reads_unclaimed)


def test_run_rule_refuses_a_record_of_another_model() -> None:
    with pytest.raises(TypeError, match="default-branch reads Gadget, not Widget"):
        run_rule(default_branch, Widget(name="w"))


def test_only_the_record_annotation_is_evaluated() -> None:
    """A deferred return annotation may name what the module imports for type checking only."""
    namespace: dict[str, object] = {"Widget": Widget}
    exec(
        "def unchecked(widget: Widget) -> Unimported:\n    '''Reads a widget.'''\n",
        namespace,
    )
    declared = rule(severity="error")(namespace["unchecked"])  # type: ignore[arg-type]
    assert declared.model is Widget


def test_a_wrapped_rule_resolves_its_model_where_it_is_defined() -> None:
    """A ``functools.wraps`` decorator from another module keeps that module's globals."""
    namespace: dict[str, object] = {}
    exec(  # the wrapper's module knows no Widget
        "import functools\n"
        "def wrap(function):\n"
        "    @functools.wraps(function)\n"
        "    def wrapper(*args):\n"
        "        return function(*args)\n"
        "    return wrapper\n",
        namespace,
    )
    wrapped = namespace["wrap"](long_summary)  # type: ignore[operator]
    assert rule(severity="error")(wrapped).model is Widget
