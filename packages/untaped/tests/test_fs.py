"""Behavioral tests for filesystem input helpers."""

import os
import stat
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

import untaped.fs as fs_module
from untaped.errors import ConfigError
from untaped.fs import (
    atomic_write,
    file_lock,
    read_structured_file,
)


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
    with pytest.raises(ConfigError, match=r"^file .*list\.yml must contain a mapping$"):
        read_structured_file(f)


def test_read_structured_file_missing_file_is_config_error(tmp_path: Path) -> None:
    from untaped.errors import ConfigError
    from untaped.fs import read_structured_file

    with pytest.raises(ConfigError, match=r"^file not found: .*absent\.yml$"):
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


def test_atomic_write_writes_bytes_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    atomic_write(target, b"\xff\x00a\r\n", mode=0o700)
    assert target.read_bytes() == b"\xff\x00a\r\n"
    assert target.stat().st_mode & 0o777 == 0o700


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


def test_atomic_write_through_a_dangling_symlink_creates_no_directories(tmp_path: Path) -> None:
    link = tmp_path / "out.txt"
    link.symlink_to(tmp_path / "missing" / "out.txt")
    with pytest.raises(FileNotFoundError):
        atomic_write(link, "new")
    assert not (tmp_path / "missing").exists()
    link.unlink()
    link.symlink_to(tmp_path / "target.txt")
    atomic_write(link, "new")
    assert link.is_symlink()
    assert (tmp_path / "target.txt").read_text() == "new"


def test_atomic_write_creates_the_file_with_the_requested_mode(tmp_path: Path) -> None:
    target = tmp_path / "private.txt"
    atomic_write(target, "secret", mode=0o600)
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.read_text(encoding="utf-8") == "secret"


# ---- file_lock ---------------------------------------------------------------


def _locked(path: Path, *, timeout: float) -> AbstractContextManager[None]:
    return file_lock(
        path, timeout=timeout, error=ConfigError, busy="busy lock", failed="broken lock"
    )


def test_file_lock_holds_the_lock_file_and_releases_it(tmp_path: Path) -> None:
    lock = tmp_path / "state.lock"

    with _locked(lock, timeout=1):
        assert lock.exists()
    with _locked(lock, timeout=0):
        pass


def test_file_lock_contention_raises_the_busy_message(tmp_path: Path) -> None:
    lock = tmp_path / "state.lock"

    with (
        _locked(lock, timeout=1),
        pytest.raises(ConfigError) as excinfo,
        _locked(lock, timeout=0),
    ):
        pass  # pragma: no cover - never acquired
    assert str(excinfo.value) == "busy lock"
    assert isinstance(excinfo.value.__cause__, TimeoutError)


def test_file_lock_os_failure_raises_the_failed_message_with_the_reason(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")

    with (
        pytest.raises(ConfigError, match=r"^broken lock: .+"),
        _locked(blocker / "state.lock", timeout=0),
    ):
        pass  # pragma: no cover - never acquired


def test_file_lock_leaves_errors_from_the_body_alone(tmp_path: Path) -> None:
    with (
        pytest.raises(TimeoutError, match="from the body"),
        _locked(tmp_path / "state.lock", timeout=0),
    ):
        raise TimeoutError("from the body")


# ---- read_structured_file with a CLI flag ------------------------------------


def test_read_structured_file_parses_json_by_suffix_even_with_tabs(tmp_path: Path) -> None:
    jsn = tmp_path / "vars.json"
    jsn.write_text('{\n\t"env": "stage",\n\t"tags": ["a"]\n}\n')

    assert read_structured_file(jsn, flag="--vars-file") == {"env": "stage", "tags": ["a"]}


@pytest.mark.parametrize("name", ["empty.yml", "empty.json"])
def test_read_structured_file_blank_document_is_empty(tmp_path: Path, name: str) -> None:
    empty = tmp_path / name
    empty.write_text("\n")

    assert read_structured_file(empty) == {}


def test_read_structured_file_expands_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "vars.yml").write_text("env: prod\n")

    assert read_structured_file(Path("~/vars.yml"), flag="--vars-file") == {"env": "prod"}


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("vars.yml", None, "--args-file file not found: {path}"),
        ("vars.yml", "[unclosed\n", "--args-file file {path} is invalid YAML: "),
        ("vars.json", "{nope", "--args-file file {path} is invalid JSON: "),
        ("vars.yml", "- a\n", "--args-file file {path} must contain a mapping"),
        (
            "vars.yml",
            "2: x\ntrue: y\nok: 1\n",
            "--args-file file {path}: keys must be strings (got 2, True)",
        ),
        ("vars.yml", b"\xff\xfe", "could not read --args-file file {path}: 'utf-8' codec"),
    ],
    ids=["missing", "invalid-yaml", "invalid-json", "not-a-mapping", "non-string-keys", "not-utf8"],
)
def test_read_structured_file_errors_name_the_flag_and_file(
    tmp_path: Path, name: str, content: str | bytes | None, message: str
) -> None:
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content)

    with pytest.raises(ConfigError) as excinfo:
        read_structured_file(path, flag="--args-file")
    assert message.format(path=path) in str(excinfo.value)


def test_read_structured_file_unreadable_is_not_reported_as_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"could not read --vars-file file .*: Is a directory"):
        read_structured_file(tmp_path, flag="--vars-file")
