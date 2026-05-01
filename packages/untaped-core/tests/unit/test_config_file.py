"""Tests for the YAML config-file helpers."""

from pathlib import Path

import pytest
from untaped_core.config_file import (
    MISSING,
    delete_profile,
    get_active_profile_name,
    get_at_path,
    list_profile_names,
    parse_key,
    read_config_dict,
    read_profile,
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
