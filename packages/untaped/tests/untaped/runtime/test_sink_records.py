"""``diag.info(records)``: rows printed as one table on stderr, laid out as stdout's."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from typing import Annotated

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import quiet
from untaped.runtime.run import entered, start_run
from untaped.runtime.sink import Diagnostics
from untaped.sdk import Column


class Change(BaseModel):
    path: Annotated[str, Column]
    action: Annotated[str, Column]
    note: Annotated[str | None, Column] = None
    internal: str = "never a column"


_ROWS = [Change(path="a.txt", action="create"), Change(path="b.txt", action="update")]


def _stderr(*items: object, unmuted: bool = False, muted: bool = False) -> str:
    stream = io.StringIO()
    with redirect_stderr(stream), entered(start_run()) as run:
        if muted:
            quiet(run)
        for item in items:
            Diagnostics().info(item, unmuted=unmuted)  # type: ignore[arg-type]
    return stream.getvalue()


def test_records_print_as_one_table_of_their_columns_with_empty_ones_left_out() -> None:
    cells = [
        [cell.strip() for cell in line.strip("│").split("│")]
        for line in _stderr(_ROWS).splitlines()
        if line.startswith("│")
    ]

    assert cells == [["path", "action"], ["a.txt", "create"], ["b.txt", "update"]]


def test_the_list_view_prints_each_record_as_key_value_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__UI__COLLECTION_VIEW", "list")

    printed = _stderr(_ROWS)

    assert "path: a.txt" in printed
    assert "action: update" in printed


def test_no_records_print_nothing_and_quiet_mutes_them_unless_unmuted() -> None:
    assert _stderr([]) == ""
    assert _stderr(_ROWS, muted=True) == ""
    assert "a.txt" in _stderr(_ROWS, muted=True, unmuted=True)


def test_under_json_diagnostics_the_table_is_one_info_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    lines = [json.loads(line) for line in _stderr(_ROWS).splitlines()]

    assert [line["level"] for line in lines] == ["info"]
    assert "a.txt" in lines[0]["message"]
