"""Default-columns lint: a wide record collection picks its table columns.

A collection of records with more than four fields (``error`` aside) must
have default table columns: the record type's ``table_columns`` or the
command's ``emit(..., table_columns=…)`` (``docs/conventions.md``). The
suite-wide ``table_default_violations`` fixture (``tests/conftest.py``)
fails a test whose command emits such a collection without them, unless
``baselines/table_defaults/<owner>.txt`` lists the record type; this module
checks that every listed type still lacks them, so each baseline can only
shrink.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from untaped.records import table_columns_of
from untaped.sdk import OutcomeRecord, emit

BASELINES = Path(__file__).parent / "baselines" / "table_defaults"
RULE = "::no-default-columns"


def _still_lacks_defaults(path: str) -> bool:
    module, _, name = path.rpartition(".")
    model = getattr(importlib.import_module(module), name, None)
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        return False
    fields = {*model.model_fields, *model.model_computed_fields} - {"error"}
    return len(fields) > 4 and not table_columns_of(model)


def test_listed_record_types_still_lack_default_columns(baseline: Any) -> None:
    found: dict[str, list[str]] = defaultdict(list)
    for file in sorted(BASELINES.glob("*.txt")):
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.endswith(RULE) and _still_lacks_defaults(line.removesuffix(RULE)):
                found[file.stem].append(line)
    baseline("table_defaults", found)


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
