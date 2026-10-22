"""Core's data file: the stamp, the object and line layouts, and what a read can answer."""

from __future__ import annotations

import errno
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from untaped import data_file, fs
from untaped.data_file import (
    Absent,
    Found,
    Lines,
    OtherVersion,
    Unreadable,
    copy_data_file,
    read_data_file,
    read_lines,
    type_hash,
    write_data_file,
    write_lines,
    written_by,
)
from untaped.errors import ConfigError


class Mark(BaseModel):
    history: Literal["partial", "complete"] = "partial"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


WHEN = datetime(2026, 10, 21, 9, 12, 3, tzinfo=UTC)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- the object layout ---------------------------------------------------------------


def test_a_value_round_trips_with_its_stamp_first_and_its_fields_at_the_top(
    tmp_path: Path,
) -> None:
    file = tmp_path / "deep" / "untaped-workspace.json"
    assert write_data_file(file, Mark(history="complete"), refreshed_at=WHEN) is True

    raw = json.loads(file.read_text(encoding="utf-8"))
    assert list(raw) == ["untaped", "history"]
    assert raw == {
        "untaped": {"file": 1, "by": written_by(), "refreshed_at": "2026-10-21T09:12:03Z"},
        "history": "complete",  # where 11.0 reads it, with .get("history")
    }
    assert _mode(file) == 0o600
    assert read_data_file(file, Mark) == Found(
        Mark(history="complete"), refreshed_at=WHEN, by=written_by()
    )


def test_without_refreshed_at_the_stamp_has_none(tmp_path: Path) -> None:
    file = tmp_path / "mark.json"
    write_data_file(file, Mark())
    assert json.loads(file.read_text())["untaped"] == {"file": 1, "by": written_by()}
    assert read_data_file(file, Mark) == Found(Mark(), by=written_by())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{not json", Unreadable("is not valid JSON")),
        ("[1, 2]", Unreadable("is not a JSON object")),
        ('{"history": "complete"}', Unreadable("has no 'untaped' stamp")),
        ('{"untaped": {"file": "one"}}', Unreadable("its 'untaped' stamp names no format")),
        (
            '{"untaped": {"file": 1}, "history": "lost"}',
            Unreadable("does not hold a Mark: history: Input should be 'partial' or 'complete'"),
        ),
        ('{"untaped": {"file": 2, "by": "12.0.0"}, "x": 1}', OtherVersion(file=2, by="12.0.0")),
    ],
)
def test_each_way_a_file_can_fail_to_read(tmp_path: Path, text: str, expected: object) -> None:
    file = tmp_path / "mark.json"
    file.write_text(text, encoding="utf-8")
    assert read_data_file(file, Mark) == expected


def test_a_missing_file_or_folder_is_absent(tmp_path: Path) -> None:
    assert read_data_file(tmp_path / "nope.json", Mark) == Absent()
    (tmp_path / "file").write_text("x")
    assert read_data_file(tmp_path / "file" / "below.json", Mark) == Absent()


def test_an_unstamped_file_is_read_in_the_models_shape_when_accepted(tmp_path: Path) -> None:
    """11.0 wrote its files without a stamp, in the shape its model reads."""
    file = tmp_path / "untaped-workspace.json"
    file.write_text('{"history": "complete"}', encoding="utf-8")
    assert read_data_file(file, Mark, unstamped="accept") == Found(Mark(history="complete"))


def test_a_plain_string_untaped_is_data_unless_it_is_a_legacy_stamp(tmp_path: Path) -> None:
    file = tmp_path / "project.json"
    file.write_text('{"untaped": "1", "id": "3f9c0a1b2d4e"}', encoding="utf-8")
    assert read_data_file(file, Strict) == Unreadable("has no 'untaped' stamp")
    accepted = read_data_file(file, Strict, unstamped="accept")
    assert isinstance(accepted, Unreadable) and "untaped" in accepted.reason  # data, forbidden
    assert read_data_file(file, Strict, legacy_stamps=("1",)) == Found(Strict(id="3f9c0a1b2d4e"))
    assert read_data_file(file, Strict, legacy_stamps=("2",)) == Unreadable(
        "has no 'untaped' stamp"
    )


class Clashing(BaseModel):
    untaped: str = "x"


class Aliased(BaseModel):
    version: str = Field(default="x", alias="untaped")


@pytest.mark.parametrize("model", [Clashing, Aliased])
def test_a_model_with_a_field_named_untaped_is_refused(
    tmp_path: Path, model: type[BaseModel]
) -> None:
    with pytest.raises(ConfigError, match="keeps its stamp under 'untaped'"):
        write_data_file(tmp_path / "x.json", model())
    with pytest.raises(ConfigError, match="keeps its stamp under 'untaped'"):
        read_data_file(tmp_path / "x.json", model)


def test_an_exclusive_write_creates_the_file_whole_only_when_absent(tmp_path: Path) -> None:
    file = tmp_path / ".untaped" / "project.json"
    assert write_data_file(file, Strict(id="a"), exclusive=True) is True
    assert write_data_file(file, Strict(id="b"), exclusive=True) is False
    assert read_data_file(file, Strict) == Found(Strict(id="a"), by=written_by())
    assert _mode(file) == 0o600
    assert [path.name for path in file.parent.iterdir()] == ["project.json"]  # no temp left


def test_without_hard_links_an_exclusive_create_stands_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_link(*_args: object) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    file = tmp_path / "project.json"
    assert write_data_file(file, Strict(id="a"), exclusive=True) is True
    assert write_data_file(file, Strict(id="b"), exclusive=True) is False
    assert read_data_file(file, Strict) == Found(Strict(id="a"), by=written_by())
    assert _mode(file) == 0o600


def test_a_copy_keeps_the_data_and_clears_its_freshness(tmp_path: Path) -> None:
    source, target = tmp_path / "a.json", tmp_path / "b" / "a.json"
    write_data_file(source, Mark(history="complete"), refreshed_at=WHEN)
    copy_data_file(source, target)
    assert read_data_file(target, Mark) == Found(Mark(history="complete"), by=written_by())
    assert _mode(target) == 0o600


def test_found_names_the_untaped_its_stamp_names(tmp_path: Path) -> None:
    file = tmp_path / "mark.json"
    file.write_text('{"untaped": {"file": 1, "by": "11.3.0"}}', encoding="utf-8")
    assert read_data_file(file, Mark) == Found(Mark(), by="11.3.0")
    file.write_text('{"untaped": {"file": 1}}', encoding="utf-8")
    assert read_data_file(file, Mark) == Found(Mark())  # a stamp that names none


def test_a_copy_of_an_unstamped_file_gets_this_releases_stamp(tmp_path: Path) -> None:
    source, target = tmp_path / "a.json", tmp_path / "b.json"
    source.write_text('{"history": "complete", "fetched": 1}', encoding="utf-8")
    copy_data_file(source, target)
    assert json.loads(target.read_text()) == {
        "untaped": {"file": 1, "by": written_by()},
        "history": "complete",
        "fetched": 1,
    }
    source.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="is not a JSON object"):
        copy_data_file(source, target)


def test_a_copy_refuses_a_newer_or_a_broken_stamp(tmp_path: Path) -> None:
    source, target = tmp_path / "a.json", tmp_path / "b.json"
    source.write_text('{"untaped": {"file": 2, "by": "12.0.0"}, "x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"newer untaped \(12\.0\.0\)"):
        copy_data_file(source, target)
    source.write_text('{"untaped": {"file": "one"}, "x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="names no format"):
        copy_data_file(source, target)
    assert not target.exists()


def test_a_filesystem_that_refuses_modes_still_gets_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some CIFS and FUSE mounts refuse fchmod outright."""
    real = os.chmod

    def refuse(target: object, mode: int, **kwargs: object) -> None:
        if isinstance(target, int):
            raise OSError(errno.EPERM, "Operation not permitted")
        real(target, mode, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(fs.os, "chmod", refuse)
    file = tmp_path / "mark.json"
    write_data_file(file, Mark())
    assert read_data_file(file, Mark) == Found(Mark(), by=written_by())

    def broken(target: object, mode: int, **kwargs: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(fs.os, "chmod", broken)
    with pytest.raises(OSError, match="Input/output error"):
        write_data_file(file, Mark())


# --- the line layout -----------------------------------------------------------------


def test_lines_keep_the_stamp_and_the_value_bytes_apart(tmp_path: Path) -> None:
    file = tmp_path / "key.v1.json"
    write_lines(file, {"key": "k", "refreshed_at": None}, b'{"items": [1]}')
    assert file.read_bytes().count(b"\n") == 2
    found = read_lines(file)
    assert isinstance(found, Lines)
    assert dict(found.stamp) == {"file": 1, "by": written_by(), "key": "k", "refreshed_at": None}
    assert found.value == b'{"items": [1]}'
    assert _mode(file) == 0o600

    head = read_lines(file, value=False)
    assert isinstance(head, Lines) and (head.value, head.holds) == (None, True)
    assert head.stamp == found.stamp

    write_lines(file, {"key": "k"}, None)
    for each in (read_lines(file), read_lines(file, value=False)):
        assert isinstance(each, Lines) and (each.value, each.holds) == (None, False)


def test_lines_tell_a_newer_and_a_damaged_file_apart(tmp_path: Path) -> None:
    file = tmp_path / "key.json"
    assert read_lines(file) == Absent()
    file.write_text('{"untaped": {"file": 9, "by": "13.0.0"}}\n[]\n')
    assert read_lines(file) == OtherVersion(file=9, by="13.0.0")
    file.write_text("garbage\n")
    assert isinstance(read_lines(file), Unreadable)
    file.write_text('{"key": "k"}\n')
    assert read_lines(file) == Unreadable("its first line has no 'untaped' stamp")


# --- the type hash -------------------------------------------------------------------


class Repo(BaseModel):
    """A repo."""

    name: str
    url: str = Field(description="where it is cloned from")


class Same(BaseModel):
    """Another docstring, another title, another order and descriptions."""

    url: str = Field(description="something else", examples=["https://example.com"])
    name: str


class Wider(BaseModel):
    name: str
    url: str
    branch: str = "main"


class Titled(BaseModel):
    title: str
    description: str


class Holder(BaseModel):
    repo: Repo


class RepoRenamed(BaseModel):
    name: str
    url: str


class HolderRenamed(BaseModel):
    repo: RepoRenamed


def _hash(model: type[BaseModel]) -> str:
    return type_hash(model.model_json_schema())


def test_the_type_hash_ignores_what_describes_a_type_and_keeps_its_shape() -> None:
    assert _hash(Repo) == _hash(Same)
    assert _hash(Repo) != _hash(Wider)
    assert _hash(Holder) != _hash(HolderRenamed)  # a nested class's name is its $defs key
    assert len(_hash(Repo)) == 16


def test_fields_named_title_or_description_are_part_of_the_shape() -> None:
    class Untitled(BaseModel):
        heading: str
        description: str

    assert "title" in json.dumps(data_file._shape(Titled.model_json_schema()))
    assert _hash(Titled) != _hash(Untitled)


def test_an_exclusive_create_s_temp_file_is_a_write_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked: list[str] = []
    real = os.link

    def spy(source: str, target: str) -> None:
        linked.append(Path(source).name)
        real(source, target)

    monkeypatch.setattr(os, "link", spy)
    write_data_file(tmp_path / "project.json", Strict(id="a"), exclusive=True)
    assert [fs.is_write_temp(name) for name in linked] == [True]
