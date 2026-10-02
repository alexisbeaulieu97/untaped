"""The ``table_default_violations`` fixture on synthetic records.

A collection of records with more than four fields (``error`` aside) must
have default table columns: the record type's ``table_columns`` or the
command's ``emit(..., table_columns=…)`` (``docs/conventions.md``). The
suite-wide fixture (root ``conftest.py``) fails any test whose command
emits such a collection without them.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from untaped.sdk import OutcomeRecord, emit


class _Wide(OutcomeRecord):
    a: str
    b: str = ""
    c: str = ""
    d: str = ""


_Wide.__module__ = "untaped.capabilities.example.domain"


class _Narrowed(_Wide):
    table_columns: ClassVar[tuple[str, ...]] = ("a", "action")


_Narrowed.__module__ = _Wide.__module__


def test_a_wide_collection_without_default_columns_is_flagged(
    table_default_violations: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Wide(a="x", action="created")], fmt="json")
    flagged = list(table_default_violations)
    table_default_violations.clear()  # handled here: do not fail this test
    assert flagged == ["untaped.capabilities.example.domain._Wide::no-default-columns"]


@pytest.mark.parametrize(
    ("records", "table_columns"),
    [
        ([_Narrowed(a="x", action="created")], None),
        ([_Wide(a="x", action="created")], ["a"]),
        (_Wide(a="x", action="created"), None),
    ],
    ids=["record-defaults", "command-defaults", "single-record"],
)
def test_default_columns_or_a_single_record_pass(
    table_default_violations: list[str],
    capsys: pytest.CaptureFixture[str],
    records: Any,
    table_columns: list[str] | None,
) -> None:
    emit(records, fmt="json", table_columns=table_columns)
    assert table_default_violations == []


@pytest.mark.parametrize(
    ("module", "flagged"),
    [
        ("untaped_acme.domain", True),
        ("untaped.capabilities.x", True),
        ("untapedish.x", False),
        ("acme.x", False),
    ],
)
def test_only_untaped_and_untaped_underscore_modules_own_their_records(
    table_default_violations: list[str],
    capsys: pytest.CaptureFixture[str],
    module: str,
    flagged: bool,
) -> None:
    record = type("_Foreign", (_Wide,), {"__module__": module})
    emit([record(a="x", action="created")], fmt="json")
    found = list(table_default_violations)
    table_default_violations.clear()  # handled here: do not fail this test
    assert found == ([f"{module}._Foreign::no-default-columns"] if flagged else [])
