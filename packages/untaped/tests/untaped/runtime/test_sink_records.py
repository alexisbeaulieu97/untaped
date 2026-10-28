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


class Target(BaseModel):
    target: Annotated[str, Column]
    files: Annotated[int, Column]


def test_records_of_several_models_keep_each_ones_fields() -> None:
    printed = _stderr([Change(path="a.txt", action="create"), Target(target="repo", files=2)])

    assert "a.txt" in printed
    assert "repo" in printed
    assert "files" in printed


def test_a_hint_follows_the_table_and_an_empty_string_column_is_left_out() -> None:
    stream = io.StringIO()
    with redirect_stderr(stream), entered(start_run()):
        Diagnostics().info([Change(path="a.txt", action="create", note="")], hint="run it again")

    printed = stream.getvalue()
    assert printed.rstrip().endswith("hint: run it again")
    assert "note" not in printed


def test_a_long_detail_wraps_where_a_long_cell_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed(BaseModel):
        target: Annotated[str, Column]
        detail: Annotated[str, Column]

    monkeypatch.setenv("COLUMNS", "60")
    why = "hook failed: command 'make lint' exited 2 after printing a long explanation"

    printed = _stderr([Failed(target="t" * 80, detail=why)])

    assert "…" in printed  # the target is cut to fit
    assert "explanation" in printed  # the detail wraps, whole


def test_json_diagnostics_carry_plain_key_value_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    (line,) = [json.loads(line) for line in _stderr(_ROWS).splitlines()]

    assert "path: a.txt" in line["message"]
    assert "│" not in line["message"]
