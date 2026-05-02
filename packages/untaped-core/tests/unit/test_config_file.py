"""Tests for the YAML config-file helpers."""

from pathlib import Path
from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_core.config_file import (
    MISSING,
    delete_profile,
    get_active_profile_name,
    get_at_path,
    list_profile_names,
    mutate_config,
    parse_key,
    read_config_dict,
    read_profile,
    rename_profile,
    set_active_profile,
    set_at_path,
    unset_at_path,
    write_config_dict,
    write_profile,
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
    assert parse_key("awx.token") == ("awx", "token")


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


# ---------------------------- profile helpers ---------------------------- #


def test_list_profile_names_empty_when_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    assert list_profile_names() == []


def test_list_profile_names_returns_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}, "stage": {}}})
    assert sorted(list_profile_names()) == ["default", "prod", "stage"]


def test_get_active_profile_name_returns_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}}, "active": "prod"})
    assert get_active_profile_name() == "prod"


def test_get_active_profile_name_returns_none_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    assert get_active_profile_name() is None


def test_get_active_profile_name_returns_none_when_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}, "active": ""})
    assert get_active_profile_name() is None


def test_set_active_profile_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}}})
    set_active_profile("prod")
    assert read_config_dict()["active"] == "prod"


def test_set_active_profile_does_not_validate_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Low-level helper does not enforce policy; that is the use case's job."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    set_active_profile("not-a-real-profile")
    assert read_config_dict()["active"] == "not-a-real-profile"


def test_read_profile_returns_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"prod": {"awx": {"token": "x"}}}})
    assert read_profile("prod") == {"awx": {"token": "x"}}


def test_read_profile_returns_none_for_missing_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    assert read_profile("nope") is None


def test_read_profile_returns_none_when_no_profiles_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    assert read_profile("default") is None


def test_write_profile_creates_when_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_profile("prod", {"awx": {"token": "x"}})
    assert read_config_dict() == {"profiles": {"prod": {"awx": {"token": "x"}}}}


def test_write_profile_replaces_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"prod": {"awx": {"token": "old"}}}})
    write_profile("prod", {"awx": {"token": "new"}})
    assert read_config_dict()["profiles"]["prod"] == {"awx": {"token": "new"}}


def test_write_profile_preserves_other_profiles_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict(
        {
            "profiles": {"default": {"a": 1}, "prod": {"b": 2}},
            "active": "prod",
            "workspace": {"workspaces": [{"name": "x", "path": "/p"}]},
        }
    )
    write_profile("prod", {"b": 3})
    data = read_config_dict()
    assert data["profiles"]["default"] == {"a": 1}
    assert data["profiles"]["prod"] == {"b": 3}
    assert data["active"] == "prod"
    assert data["workspace"] == {"workspaces": [{"name": "x", "path": "/p"}]}


def test_delete_profile_removes_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}}})
    assert delete_profile("prod") is True
    assert "prod" not in read_config_dict()["profiles"]
    assert "default" in read_config_dict()["profiles"]


def test_delete_profile_returns_false_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    assert delete_profile("nope") is False


def test_delete_profile_returns_false_when_no_profiles_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    assert delete_profile("anything") is False


# ---------------------------- mutate_config ---------------------------- #


def test_mutate_config_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: callback receives the loaded dict, mutates in place, gets persisted."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {"log_level": "INFO"}}})

    def _set_debug(data: dict[str, Any]) -> None:
        data["profiles"]["default"]["log_level"] = "DEBUG"

    mutate_config(_set_debug)
    assert read_config_dict()["profiles"]["default"]["log_level"] == "DEBUG"


def test_mutate_config_clears_get_settings_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful write must invalidate ``get_settings`` so the next
    reader sees the new values without manual ``cache_clear()``."""
    from untaped_core import get_settings

    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {"log_level": "INFO"}}})

    get_settings.cache_clear()
    assert get_settings().log_level == "INFO"

    def _set_debug(data: dict[str, Any]) -> None:
        data["profiles"]["default"]["log_level"] = "DEBUG"

    mutate_config(_set_debug)
    assert get_settings().log_level == "DEBUG"


def test_mutate_config_no_op_does_not_clear_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the callback doesn't change anything, the cache must stay warm
    — clearing it on every call would defeat the cache for read-mostly
    flows like ``config list``."""
    from untaped_core import get_settings

    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {"log_level": "INFO"}}})

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
        data["active"] = "default"
        data["profiles"] = {"default": {}}

    mutate_config(_seed)
    assert cfg.exists()
    assert read_config_dict() == {"active": "default", "profiles": {"default": {}}}


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
    write_config_dict({"profiles": {"default": {"log_level": "INFO"}}})
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


def test_delete_profile_does_not_touch_file_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: deleting a profile that doesn't exist used to no-op
    cleanly. After moving onto ``mutate_config`` it spuriously rewrote
    the file. The helper must skip the write."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    before_mtime = cfg.stat().st_mtime_ns

    assert delete_profile("ghost") is False
    assert cfg.stat().st_mtime_ns == before_mtime


def test_mutate_config_exception_keeps_file_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the callback raises, the on-disk file must be untouched.

    The atomic write only runs after the callback succeeds, so a half-applied
    mutation never reaches disk.
    """
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {"log_level": "INFO"}}})
    snapshot = cfg.read_text()

    def _bad(data: dict[str, Any]) -> None:
        data["profiles"]["default"]["log_level"] = "DEBUG"
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
    write_config_dict({"profiles": {"default": {}}})

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
    write_config_dict({"profiles": {"default": {}}})

    with pytest.raises(RuntimeError):
        mutate_config(lambda data: (_ for _ in ()).throw(RuntimeError("fail")))

    # Second call must succeed without timing out.
    mutate_config(lambda data: data.update({"flag": True}))
    assert read_config_dict()["flag"] is True


# ---------------------------- rename_profile ---------------------------- #


def test_rename_profile_moves_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "stage": {"awx": {"token": "x"}}}})
    rename_profile("stage", "qa")
    data = read_config_dict()
    assert "stage" not in data["profiles"]
    assert data["profiles"]["qa"] == {"awx": {"token": "x"}}


def test_rename_profile_updates_active_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the persisted ``active:`` named the renamed profile, fix the
    pointer in the same write so it never points at a missing profile."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {"a": 1}}, "active": "prod"})
    rename_profile("prod", "production")
    data = read_config_dict()
    assert data["active"] == "production"
    assert "prod" not in data["profiles"]


def test_rename_profile_leaves_unrelated_active_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}, "stage": {}}, "active": "prod"})
    rename_profile("stage", "qa")
    data = read_config_dict()
    assert data["active"] == "prod"


def test_rename_profile_raises_when_source_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}}})
    with pytest.raises(KeyError):
        rename_profile("ghost", "x")


def test_rename_profile_raises_when_target_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {}, "qa": {}}})
    with pytest.raises(ValueError, match="qa"):
        rename_profile("prod", "qa")


def test_rename_profile_is_a_single_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole rename must land in one ``mutate_config`` call so a crash
    can't leave the config half-renamed (e.g. profile copied but old still
    present, or active: pointing at a deleted profile)."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    write_config_dict({"profiles": {"default": {}, "prod": {"a": 1}}, "active": "prod"})

    calls = 0
    real_mutate = mutate_config

    def _spy(fn: Any, path: Path | None = None) -> None:
        nonlocal calls
        calls += 1
        real_mutate(fn, path)

    monkeypatch.setattr("untaped_core.config_file.mutate_config", _spy)
    rename_profile("prod", "production")
    assert calls == 1
