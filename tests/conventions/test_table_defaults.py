"""Default-columns lint: a wide record collection picks its table columns.

A collection of records with more than four fields (``error`` aside) must
have default table columns: the record type's ``table_columns`` or the
command's ``emit(..., table_columns=…)`` (``docs/conventions.md``). The
suite-wide ``table_default_violations`` fixture (``tests/conftest.py``)
fails a test whose command emits such a collection without them; this module
checks that fixture on synthetic records.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from untaped.sdk import OutcomeRecord, emit

RULE = "::no-default-columns"


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
    assert flagged == [f"untaped.capabilities.example.domain._Wide{RULE}"]


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
