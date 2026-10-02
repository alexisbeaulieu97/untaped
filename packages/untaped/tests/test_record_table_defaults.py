"""Record-level default table columns and ``TableGlyph`` (``docs/plugins.md#output-records``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel

from untaped.sdk import OutcomeRecord, TableGlyph, emit


class _Repo(OutcomeRecord):
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action")

    repo: str
    url: str = "u"


class _Plain(BaseModel):
    table_columns: ClassVar[tuple[str, ...]] = ("name",)

    name: str
    kind: str = "x"


class _Setting(OutcomeRecord):
    key: str
    value: Annotated[object, TableGlyph(none="—")] = None
    active: Annotated[bool, TableGlyph(true="✓")] = False


def _header(out: str) -> list[str]:
    return [cell.strip() for cell in out.splitlines()[1].strip("│").split("│")]


def _cells(out: str, row: int) -> list[str]:
    return [cell.strip() for cell in out.splitlines()[3 + row].strip("│").split("│")]


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.rstrip("\n")


def test_a_collection_table_shows_the_records_default_columns(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Repo(repo="a", action="created")], fmt="table")
    assert _header(_out(capsys)) == ["repo", "action"]


def test_structured_formats_keep_every_field(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Repo(repo="a", action="created")], fmt="json")
    assert json.loads(_out(capsys)) == [{"repo": "a", "action": "created", "url": "u"}]


def test_a_plain_models_default_columns_apply_too(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Plain(name="a")], fmt="table")
    assert _header(_out(capsys)) == ["name"]


def test_a_commands_table_columns_win_over_the_records(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Repo(repo="a", action="created")], fmt="table", table_columns=["repo", "url"])
    assert _header(_out(capsys)) == ["repo", "url"]


def test_column_edits_start_from_the_records_defaults(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Repo(repo="a", action="created")], fmt="table", columns=["-action"])
    assert _header(_out(capsys)) == ["repo"]


def test_question_mark_marks_the_records_defaults(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Repo(repo="a", action="created")], fmt="table", columns=["?"])
    err = capsys.readouterr().err
    assert "  repo *" in err
    assert "  url\n" in err


def test_a_single_record_keeps_every_field(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(_Repo(repo="a", action="created"), fmt="table")
    assert "url: u" in _out(capsys)


def test_a_table_shows_a_fields_glyphs(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(
        [
            _Setting(key="a", action="updated", active=True),
            _Setting(key="b", action="updated", value=1),
        ],
        fmt="table",
    )
    out = _out(capsys)
    assert _cells(out, 0) == ["a", "updated", "—", "✓"]
    assert _cells(out, 1) == ["b", "updated", "1", "False"]


def test_a_single_records_table_shows_its_glyphs(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(_Setting(key="a", action="updated", value=1, active=True), fmt="table")
    assert "active: ✓" in _out(capsys)


def test_other_formats_print_native_values(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Setting(key="a", action="updated", active=True)], fmt="raw", columns=["value,active"])
    assert _out(capsys) == "\tTrue"
    emit([_Setting(key="a", action="updated", active=True)], fmt="json")
    assert json.loads(_out(capsys))[0] == {
        "key": "a",
        "action": "updated",
        "value": None,
        "active": True,
    }


def test_a_column_unset_on_every_row_stays_hidden(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit([_Setting(key="a", action="updated")], fmt="table")
    assert _header(_out(capsys)) == ["key", "action", "active"]


class _Optional(OutcomeRecord):
    key: str
    active: Annotated[bool, TableGlyph(true="✓")] | None = None


def test_an_optional_fields_glyph_applies(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(_Optional(key="a", action="updated", active=True), fmt="table")
    assert "active: ✓" in _out(capsys)
