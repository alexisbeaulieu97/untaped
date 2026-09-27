"""Behavioral tests for filesystem input helpers."""

import os
import stat
from pathlib import Path

import pytest

import untaped.fs as fs_module
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


def test_atomic_write_writes_through_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "out.txt"
    real.parent.mkdir()
    real.write_text("old")
    link = tmp_path / "out.txt"
    link.symlink_to(real)
    atomic_write(link, "new")
    assert link.is_symlink()
    assert real.read_text() == "new"
    assert sorted(p.name for p in real.parent.iterdir()) == ["out.txt"]


def test_atomic_write_keeps_the_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    target.write_text("old")
    target.chmod(0o750)
    atomic_write(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_atomic_write_applies_an_explicit_mode(tmp_path: Path) -> None:
    target = tmp_path / "secret.yml"
    target.write_text("old")
    target.chmod(0o644)
    atomic_write(target, "new", mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    atomic_write(tmp_path / "fresh.yml", "new", mode=0o600)
    assert stat.S_IMODE((tmp_path / "fresh.yml").stat().st_mode) == 0o600


def test_atomic_write_new_file_gets_the_default_mode(tmp_path: Path) -> None:
    old_umask = os.umask(0o022)
    try:
        atomic_write(tmp_path / "out.txt", "x")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE((tmp_path / "out.txt").stat().st_mode) == 0o644


def test_atomic_write_fsyncs_the_file_and_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[str] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        synced.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(fs_module.os, "fsync", spy)
    atomic_write(tmp_path / "out.txt", "x")
    assert synced == ["file", "dir"]


def test_apply_file_changes_keeps_mode_and_writes_through_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real.sh"
    real.write_text("v1")
    real.chmod(0o750)
    link = tmp_path / "link.sh"
    link.symlink_to(real)
    apply_file_changes([FileChange(path=link, before="v1", after="v2")])
    assert link.is_symlink()
    assert real.read_text() == "v2"
    assert stat.S_IMODE(real.stat().st_mode) == 0o750


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


def test_apply_file_changes_allows_duplicate_identical_changes(tmp_path: Path) -> None:
    target = tmp_path / "same.txt"
    target.write_text("v1", encoding="utf-8")
    change = FileChange(path=target, before="v1", after="v2")

    apply_file_changes([change, change])

    assert target.read_text(encoding="utf-8") == "v2"


def test_apply_file_changes_rolls_back_non_oserror_apply_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.txt"
    first.write_text("v1", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def replace_then_interrupt(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("stop")
        real_replace(src, dst)

    monkeypatch.setattr(fs_module.os, "replace", replace_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        apply_file_changes(
            [
                FileChange(path=first, before="v1", after="v2"),
                FileChange(path=second, before="old", after="new"),
            ]
        )

    assert first.read_text(encoding="utf-8") == "v1"
    assert second.read_text(encoding="utf-8") == "old"
