"""Tests for the YAML config-file helpers."""

from pathlib import Path
from typing import Any

import pytest

from untaped import ConfigError
from untaped.config_file import (
    MISSING,
    get_at_path,
    mutate_config,
    parse_key,
    read_config_dict,
    set_at_path,
    unset_at_path,
    write_config_dict,
)


def test_read_returns_empty_when_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    assert read_config_dict() == {}


def test_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"awx": {"token": "secret", "base_url": "https://x"}})
    assert read_config_dict() == {"awx": {"token": "secret", "base_url": "https://x"}}


def test_write_creates_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "deeply" / "nested" / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"a": 1})
    assert cfg.exists()
    assert cfg.parent.is_dir()


def test_write_uses_secure_perms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"a": 1})
    mode = cfg.stat().st_mode & 0o777
    assert mode == 0o600


def test_parse_key_simple() -> None:
    assert parse_key("demo.token") == ("demo", "token")


def test_parse_key_top_level() -> None:
    assert parse_key("log_level") == ("log_level",)


def test_parse_key_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_key("")
    with pytest.raises(ValueError):
        parse_key(".foo")
    with pytest.raises(ValueError):
        parse_key("foo.")


def test_get_returns_missing_for_absent_path() -> None:
    assert get_at_path({}, ("a", "b")) is MISSING


def test_get_returns_value_for_present_path() -> None:
    assert get_at_path({"a": {"b": 42}}, ("a", "b")) == 42


def test_set_creates_intermediate_dicts() -> None:
    data: dict = {}
    set_at_path(data, ("awx", "token"), "secret")
    assert data == {"awx": {"token": "secret"}}


def test_set_replaces_non_dict_intermediate() -> None:
    data: dict = {"awx": "scalar"}
    set_at_path(data, ("awx", "token"), "secret")
    assert data == {"awx": {"token": "secret"}}


def test_unset_removes_value_and_empty_parents() -> None:
    data: dict = {"awx": {"token": "secret"}, "github": {"token": "ghp"}}
    assert unset_at_path(data, ("awx", "token")) is True
    assert data == {"github": {"token": "ghp"}}


def test_unset_keeps_non_empty_parent() -> None:
    data: dict = {"awx": {"token": "x", "base_url": "https://y"}}
    assert unset_at_path(data, ("awx", "token")) is True
    assert data == {"awx": {"base_url": "https://y"}}


def test_unset_returns_false_for_missing_path() -> None:
    data: dict = {"awx": {"base_url": "https://y"}}
    assert unset_at_path(data, ("awx", "token")) is False
    assert data == {"awx": {"base_url": "https://y"}}


# ---------------------------- mutate_config ---------------------------- #


def test_mutate_config_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: callback receives the loaded dict, mutates in place, gets persisted."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"log_level": "INFO"})

    def _set_debug(data: dict[str, Any]) -> None:
        data["log_level"] = "DEBUG"

    mutate_config(_set_debug)
    assert read_config_dict()["log_level"] == "DEBUG"


def test_mutate_config_clears_get_settings_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful write must invalidate ``get_settings`` so the next
    reader sees the new values without manual ``cache_clear()``."""
    from untaped import get_settings

    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"log_level": "INFO"})

    get_settings.cache_clear()
    assert get_settings().log_level == "INFO"

    def _set_debug(data: dict[str, Any]) -> None:
        data["log_level"] = "DEBUG"

    mutate_config(_set_debug)
    assert get_settings().log_level == "DEBUG"


def test_mutate_config_no_op_does_not_clear_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the callback doesn't change anything, the cache must stay warm
    — clearing it on every call would defeat the cache for read-mostly
    flows like ``config list``."""
    from untaped import get_settings

    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"log_level": "INFO"})

    get_settings.cache_clear()
    cached = get_settings()
    mutate_config(lambda data: None)
    assert get_settings() is cached


def test_mutate_config_creates_file_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-time use: callback sees `{}`, write creates the file."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    def _seed(data: dict[str, Any]) -> None:
        data["log_level"] = "DEBUG"

    mutate_config(_seed)
    assert cfg.exists()
    assert read_config_dict() == {"log_level": "DEBUG"}


def test_mutate_config_skips_write_when_callback_no_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A callback that doesn't mutate the dict must not touch disk.

    Otherwise commands that intentionally no-op (``config unset`` for a
    missing key, ``delete_profile`` for a missing name, ``unregister`` for
    a missing workspace) silently re-serialize the YAML — losing comments,
    losing user-set key order, and bumping mtime — even though nothing
    semantically changed.
    """
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"log_level": "INFO"})
    before_mtime = cfg.stat().st_mtime_ns

    mutate_config(lambda data: None)

    assert cfg.stat().st_mtime_ns == before_mtime


def test_mutate_config_no_op_does_not_create_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-op against a missing config must not bring the file into
    existence — the user just probed something that wasn't there."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    mutate_config(lambda data: None)

    assert not cfg.exists()


def test_mutate_config_exception_keeps_file_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the callback raises, the on-disk file must be untouched.

    The atomic write only runs after the callback succeeds, so a half-applied
    mutation never reaches disk.
    """
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"log_level": "INFO"})
    snapshot = cfg.read_text()

    def _bad(data: dict[str, Any]) -> None:
        data["log_level"] = "DEBUG"
        raise RuntimeError("oops")

    with pytest.raises(RuntimeError, match="oops"):
        mutate_config(_bad)
    assert cfg.read_text() == snapshot


def test_mutate_config_lock_blocks_concurrent_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding the lock on disk causes a contending caller to error out
    promptly with ConfigError, not silently overwrite."""
    from filelock import FileLock

    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_CONFIG_LOCK_TIMEOUT", "0.05")
    write_config_dict({"log_level": "INFO"})

    held = FileLock(str(cfg) + ".lock")
    held.acquire()
    try:
        with pytest.raises(ConfigError, match="lock"):
            mutate_config(lambda data: None)
    finally:
        held.release()


def test_mutate_config_releases_lock_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A callback that raises must still release the lock so a follow-up
    mutation isn't blocked forever."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_CONFIG_LOCK_TIMEOUT", "0.05")
    write_config_dict({"log_level": "INFO"})

    with pytest.raises(RuntimeError):
        mutate_config(lambda data: (_ for _ in ()).throw(RuntimeError("fail")))

    # Second call must succeed without timing out.
    mutate_config(lambda data: data.update({"flag": True}))
    assert read_config_dict()["flag"] is True


# ---------------------------- YAML parse errors ---------------------------- #


def test_read_config_dict_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken YAML must surface as ``ConfigError`` (user-facing ``error: …``),
    not bubble out as a PyYAML traceback to the CLI handler."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("active: [unterminated\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(ConfigError, match=str(cfg)):
        read_config_dict()


def test_mutate_config_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``untaped config set`` over a corrupted config file must surface as
    ``ConfigError`` — ``mutate_config`` reads via ``read_config_dict`` inside
    the lock, so the boundary translation must carry through."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("active: [unterminated\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(ConfigError, match=str(cfg)):
        mutate_config(lambda data: data.update({"x": 1}))
