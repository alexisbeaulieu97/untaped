"""Behavioral tests for filesystem input helpers."""

from pathlib import Path

import pytest

from untaped.fs import FileChange, FileWriteError, apply_file_changes, atomic_write


def test_read_structured_file_yaml(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "payload.yml"
    f.write_text("fields:\n  summary: hi\n", encoding="utf-8")
    assert read_structured_file(f) == {"fields": {"summary": "hi"}}


def test_read_structured_file_json_by_suffix(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "payload.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert read_structured_file(f) == {"a": 1}


def test_read_structured_file_empty_yaml_is_empty_dict(tmp_path: Path) -> None:
    from untaped.fs import read_structured_file

    f = tmp_path / "empty.yml"
    f.write_text("", encoding="utf-8")
    assert read_structured_file(f) == {}


def test_read_structured_file_rejects_non_object(tmp_path: Path) -> None:
    from untaped.errors import ConfigError
    from untaped.fs import read_structured_file

    f = tmp_path / "list.yml"
    f.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain an object"):
        read_structured_file(f)


def test_read_structured_file_missing_file_is_config_error(tmp_path: Path) -> None:
    from untaped.errors import ConfigError
    from untaped.fs import read_structured_file

    with pytest.raises(ConfigError, match="could not read"):
        read_structured_file(tmp_path / "absent.yml")


def test_atomic_write_creates_parents_and_writes(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "out.txt"
    atomic_write(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_preserves_crlf_verbatim(tmp_path: Path) -> None:
    """newline='' means no translation — CRLF content survives byte-for-byte."""
    target = tmp_path / "crlf.txt"
    atomic_write(target, "a\r\nb\r\n")
    assert target.read_bytes() == b"a\r\nb\r\n"


def test_atomic_write_leaves_no_temp_file_on_success(tmp_path: Path) -> None:
    atomic_write(tmp_path / "out.txt", "x")
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]


def test_apply_file_changes_writes_deletes_and_creates(tmp_path: Path) -> None:
    existing = tmp_path / "keep.txt"
    existing.write_text("old", encoding="utf-8")
    doomed = tmp_path / "gone.txt"
    doomed.write_text("bye", encoding="utf-8")
    apply_file_changes(
        [
            FileChange(path=existing, before="old", after="new"),
            FileChange(path=doomed, before="bye", after=None),
            FileChange(path=tmp_path / "fresh.txt", before=None, after="born"),
        ]
    )
    assert existing.read_text(encoding="utf-8") == "new"
    assert not doomed.exists()
    assert (tmp_path / "fresh.txt").read_text(encoding="utf-8") == "born"


def test_apply_file_changes_refuses_when_content_drifted(tmp_path: Path) -> None:
    target = tmp_path / "drift.txt"
    target.write_text("actual", encoding="utf-8")
    with pytest.raises(FileWriteError, match="changed since planning"):
        apply_file_changes([FileChange(path=target, before="expected", after="new")])
    assert target.read_text(encoding="utf-8") == "actual"  # untouched


def test_apply_file_changes_rolls_back_applied_changes_on_failure(tmp_path: Path) -> None:
    ok = tmp_path / "ok.txt"
    ok.write_text("v1", encoding="utf-8")
    # Second change targets an existing NON-EMPTY DIRECTORY: verification
    # passes (not a file → current None == before None), staging succeeds,
    # but the apply-phase os.replace onto the directory raises OSError —
    # exercising rollback of the already-applied first change.
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "occupant.txt").write_text("here", encoding="utf-8")
    with pytest.raises(FileWriteError):
        apply_file_changes(
            [
                FileChange(path=ok, before="v1", after="v2"),
                FileChange(path=blocker, before=None, after="never"),
            ]
        )
    assert ok.read_text(encoding="utf-8") == "v1"  # rolled back
