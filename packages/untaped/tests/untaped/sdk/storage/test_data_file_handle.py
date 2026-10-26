"""DataFile[M]: one stamped file wherever its owner places it, read and written by its model."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from filelock import FileLock
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import (
    Absent,
    ConfigError,
    DataFile,
    Found,
    OtherVersion,
    Unreadable,
    UntapedError,
)
from untaped.sdk._run import run_scope
from untaped.sdk.storage import data_file


class Mark(BaseModel):
    name: str
    size: int = 0


class Other(BaseModel):
    name: str


@pytest.fixture(autouse=True)
def home(tmp_path: Path) -> Iterator[Path]:
    """A run whose home keeps the write locks, apart from the files written."""
    home = tmp_path / "home"
    with run_scope(run_owning(home, __name__.partition(".")[0], "demo")):
        yield home


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


@pytest.mark.parametrize(
    "text",
    [
        '{"untaped": {"by": "13.0"}, "name": "ada", "later": 1}',  # a damaged stamp
        '{"untaped": {"file": 1}, "name": "ada", "size": "big", "later": 1}',  # a bad field
    ],
)
def test_a_write_over_a_damaged_file_keeps_what_the_model_does_not_know(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "mark.json"
    path.write_text(text)
    assert isinstance(DataFile[Mark].at(path).read(), Unreadable)
    DataFile[Mark].at(path).write(Mark(name="bob"))
    stored = json.loads(path.read_text())
    assert (stored["name"], stored["size"], stored["later"]) == ("bob", 0, 1)


def test_a_file_that_cannot_be_read_is_never_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mark.json"
    path.write_text('{"untaped": {"file": 2}, "name": "ada"}')
    real = Path.read_bytes

    def failing(self: Path) -> bytes:
        if self == path:
            raise OSError(5, "Input/output error", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", failing)
    with pytest.raises(ConfigError, match=r"Input/output error.*not written over"):
        DataFile[Mark].at(path).write(Mark(name="bob"))
    monkeypatch.undo()
    assert '"file": 2' in path.read_text()


def test_an_unchanged_field_keeps_its_stored_spelling(tmp_path: Path) -> None:
    class Dated(BaseModel):
        when: datetime
        note: str = ""

    path = tmp_path / "dated.json"
    path.write_text('{"untaped": {"file": 1}, "when": "2026-01-01T00:00:00+00:00"}')
    marker = DataFile[Dated].at(path)
    found = marker.read()
    assert isinstance(found, Found)
    marker.write(found.value.model_copy(update={"note": "kept"}))
    stored = json.loads(path.read_text())
    assert (stored["when"], stored["note"]) == ("2026-01-01T00:00:00+00:00", "kept")


def test_an_exclusive_write_never_replaces_a_file_made_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mark.json"
    marker = DataFile[Mark].at(path)
    real = DataFile._stored

    def raced(self: DataFile[Mark]) -> dict[str, object] | None:
        found = real(self)
        data_file.write_data_file(path, Mark(name="first"))  # another writer, unlocked
        return found

    monkeypatch.setattr(DataFile, "_stored", raced)
    assert not marker.write(Mark(name="second"), exclusive=True)
    assert json.loads(path.read_text())["name"] == "first"


def test_a_write_waits_for_another_writer_then_says_it_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mark.json"
    monkeypatch.setattr(data_file, "_WRITE_WAIT", 0)
    busy = r"mark\.json is busy \(another untaped process\)"
    with FileLock(str(data_file._lock_path(path))), pytest.raises(ConfigError, match=busy):
        DataFile[Mark].at(path).write(Mark(name="ada"))
    assert not path.exists()
    assert [each.name for each in tmp_path.iterdir()] == ["home"]  # no lock file beside it


def test_a_write_inside_a_write_to_the_same_file_is_refused(tmp_path: Path) -> None:
    from untaped.sdk.storage._fs import locked_write

    path = tmp_path / "mark.json"
    lock = data_file._lock_path(path)
    with (
        locked_write(path, timeout=0, busy="busy", lock=lock),
        pytest.raises(UntapedError, match="inside another write"),
    ):
        DataFile[Mark].at(path).write(Mark(name="ada"))


def test_a_copy_needs_a_source_that_reads_and_a_target_it_may_write(tmp_path: Path) -> None:
    unstamped = tmp_path / "unstamped.json"
    unstamped.write_text('{"name": "ada"}')
    with pytest.raises(ConfigError, match=r"cannot copy .* has no 'untaped' stamp"):
        DataFile[Mark].at(unstamped).copy_to(tmp_path / "copy.json")
    with pytest.raises(ConfigError, match=r"cannot copy .* does not exist"):
        DataFile[Mark].at(tmp_path / "absent.json").copy_to(tmp_path / "copy.json")
    source = DataFile[Mark].at(tmp_path / "a.json")
    source.write(Mark(name="ada"))
    newer = tmp_path / "newer.json"
    newer.write_text('{"untaped": {"file": 2}, "name": "zed"}')
    with pytest.raises(ConfigError, match="written by a newer untaped"):
        source.copy_to(newer)
    assert '"file": 2' in newer.read_text()


def test_a_copy_keeps_the_sources_unknown_keys_and_not_the_targets(tmp_path: Path) -> None:
    source = tmp_path / "a.json"
    source.write_text('{"untaped": {"file": 1}, "name": "ada", "later": 1}')
    target = tmp_path / "b.json"
    target.write_text('{"untaped": {"file": 1}, "name": "old", "stale": 1}')
    DataFile[Mark].at(source).copy_to(target)
    stored = json.loads(target.read_text())
    assert (stored["name"], stored["later"], "stale" in stored) == ("ada", 1, False)
