"""Integration-ish tests for the YAML-backed ``ProfileFileRepository``.

These exercise the file I/O paths the in-memory fake doesn't cover: round
trips through ``~/.untaped/config.yml``, ``resolved()`` merging, and the
``get_settings`` cache invalidation that mutating methods trigger.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_core import get_settings
from untaped_profile.infrastructure import ProfileFileRepository


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n    awx:\n      api_prefix: /api/v2/\n"
        "  prod:\n    awx:\n      base_url: https://prod\n"
        "active: prod\n"
    )


def test_names_lists_profiles(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert sorted(ProfileFileRepository().names()) == ["default", "prod"]


def test_active_name_returns_value(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert ProfileFileRepository().active_name() == "prod"


def test_active_name_is_none_when_unset(_isolate_config: Path) -> None:
    _isolate_config.write_text("profiles:\n  default: {}\n")
    assert ProfileFileRepository().active_name() is None


def test_active_name_honours_env_override(
    _isolate_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``UNTAPED_PROFILE`` env override must affect ``active_name()`` so
    the ✓ marker in `profile list` stays consistent with what other
    commands resolve."""
    _seed(_isolate_config)
    monkeypatch.setenv("UNTAPED_PROFILE", "default")
    assert ProfileFileRepository().active_name() == "default"


def test_read_returns_raw_data(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert ProfileFileRepository().read("prod") == {"awx": {"base_url": "https://prod"}}


def test_read_missing_returns_none(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert ProfileFileRepository().read("ghost") is None


def test_resolved_merges_default_and_named(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    resolved = ProfileFileRepository().resolved("prod")
    assert resolved == {
        "log_level": "INFO",
        "awx": {"api_prefix": "/api/v2/", "base_url": "https://prod"},
    }


def test_resolved_for_default_profile(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert ProfileFileRepository().resolved("default") == {
        "log_level": "INFO",
        "awx": {"api_prefix": "/api/v2/"},
    }


def test_write_persists_and_clears_cache(_isolate_config: Path) -> None:
    _isolate_config.write_text("profiles:\n  default: {}\n")
    repo = ProfileFileRepository()
    repo.write("homelab", {"awx": {"base_url": "https://lan"}})
    assert repo.read("homelab") == {"awx": {"base_url": "https://lan"}}


def test_delete_removes_profile_and_returns_true(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    repo = ProfileFileRepository()
    assert repo.delete("prod") is True
    assert "prod" not in repo.names()


def test_delete_unknown_returns_false(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    assert ProfileFileRepository().delete("ghost") is False


def test_set_active_persists(_isolate_config: Path) -> None:
    _seed(_isolate_config)
    repo = ProfileFileRepository()
    repo.set_active("default")
    assert repo.active_name() == "default"
