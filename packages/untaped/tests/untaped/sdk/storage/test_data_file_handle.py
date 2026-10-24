"""DataFile[M]: one stamped file wherever its owner places it, read and written by its model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import Absent, ConfigError, DataFile, Found, OtherVersion, Unreadable
from untaped.sdk.storage import data_file


class Mark(BaseModel):
    name: str
    size: int = 0


class Other(BaseModel):
    name: str


def test_a_written_file_reads_back_found(tmp_path: Path) -> None:
    marker = DataFile[Mark].at(tmp_path / "mark.json")
    assert marker.read() == Absent()
    assert marker.write(Mark(name="ada", size=1))
    found = marker.read()
    assert isinstance(found, Found)
    assert found.value == Mark(name="ada", size=1)
    assert found.by == data_file.written_by()
    stored = json.loads((tmp_path / "mark.json").read_text())
    assert stored["untaped"]["file"] == data_file.FILE
    assert (tmp_path / "mark.json").stat().st_mode & 0o777 == 0o600


def test_refreshed_at_goes_in_the_stamp(tmp_path: Path) -> None:
    marker = DataFile[Mark].at(tmp_path / "mark.json")
    when = datetime(2026, 10, 24, 1, 2, 3, tzinfo=UTC)
    marker.write(Mark(name="ada"), refreshed_at=when)
    found = marker.read()
    assert isinstance(found, Found)
    assert found.refreshed_at == when


def test_a_newer_untapeds_file_reads_as_other_version_and_is_never_overwritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mark.json"
    path.write_text('{"untaped": {"file": 2, "by": "13.0.0"}, "name": "ada"}')
    marker = DataFile[Mark].at(path)
    assert marker.read() == OtherVersion(file=2, by="13.0.0")
    with pytest.raises(ConfigError, match="written by a newer untaped"):
        marker.write(Mark(name="bob"))
    assert "13.0.0" in path.read_text()


def test_a_write_keeps_the_keys_the_model_does_not_know(tmp_path: Path) -> None:
    path = tmp_path / "mark.json"
    path.write_text('{"untaped": {"file": 1, "by": "12.1.0"}, "name": "ada", "later": [1]}')
    DataFile[Mark].at(path).write(Mark(name="bob", size=2))
    stored = json.loads(path.read_text())
    assert (stored["name"], stored["size"], stored["later"]) == ("bob", 2, [1])


def test_an_unstamped_file_is_not_this_models_data_file(tmp_path: Path) -> None:
    path = tmp_path / "mark.json"
    path.write_text('{"name": "ada"}')
    assert DataFile[Mark].at(path).read() == Unreadable("has no 'untaped' stamp")


def test_an_exclusive_write_writes_only_when_no_file_is_there(tmp_path: Path) -> None:
    marker = DataFile[Mark].at(tmp_path / "mark.json")
    assert marker.write(Mark(name="ada"), exclusive=True)
    assert not marker.write(Mark(name="bob"), exclusive=True)
    found = marker.read()
    assert isinstance(found, Found)
    assert found.value.name == "ada"


def test_a_value_of_another_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="keeps a Mark, not a Other"):
        DataFile[Mark].at(tmp_path / "mark.json").write(Other(name="x"))  # type: ignore[arg-type]


def test_a_failed_write_is_a_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(path: Path, *_: object, **__: object) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(data_file, "atomic_write", refuse)
    with pytest.raises(ConfigError, match=r"could not write .*Permission denied; nothing changed"):
        DataFile[Mark].at(tmp_path / "mark.json").write(Mark(name="ada"))


def test_a_copy_is_a_data_file_at_the_target(tmp_path: Path) -> None:
    source = DataFile[Mark].at(tmp_path / "a.json")
    source.write(Mark(name="ada"), refreshed_at=datetime(2026, 1, 1, tzinfo=UTC))
    copy = source.copy_to(tmp_path / "b.json")
    assert copy == DataFile[Mark].at(tmp_path / "b.json")
    found = copy.read()
    assert isinstance(found, Found)
    assert (found.value.name, found.refreshed_at) == ("ada", None)


def test_the_model_is_named_and_may_not_use_the_stamps_key(tmp_path: Path) -> None:
    class Stamped(BaseModel):
        untaped: str

    with pytest.raises(ConfigError, match=r"DataFile\[Mark\]\.at\(path\)"):
        DataFile.at(tmp_path / "x.json")
    with pytest.raises(ConfigError, match="keeps its stamp under 'untaped'"):
        DataFile[Stamped].at(tmp_path / "x.json")
    assert repr(DataFile[Mark].at(Path("/x/mark.json"))) == "DataFile[Mark].at('/x/mark.json')"
