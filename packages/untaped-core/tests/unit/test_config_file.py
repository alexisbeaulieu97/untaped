"""Tests for the YAML config-file helpers."""

from pathlib import Path

import pytest
from untaped_core.config_file import (
    MISSING,
    get_at_path,
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
