"""Tests for config-backed alias and source repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.capabilities.ansible.infrastructure import AliasRepository, SourceRepository
from untaped.capabilities.ansible.settings import SourceDefinition
from untaped.capability_api import ConfigError


def _read_state(cfg: Path) -> dict[str, Any]:
    """The parsed ``state.yml`` next to ``cfg`` (``{}`` when not written)."""
    state_file = cfg.parent / "state.yml"
    if not state_file.exists():
        return {}
    return yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}


def _write_config(tmp_path: Path, ansible_state: dict[str, object]) -> Path:
    """Seed ``state.yml`` with the ansible state; return the (empty) config path."""
    state_file = tmp_path / "state.yml"
    state_file.write_text(
        yaml.safe_dump({"ansible": ansible_state}, sort_keys=False), encoding="utf-8"
    )
    return tmp_path / "config.yml"


def test_alias_repository_rejects_non_mapping_alias_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"aliases": ["common"]})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with pytest.raises(ConfigError, match=r"`ansible\.aliases` must be a mapping"):
        AliasRepository().entries()


def test_alias_repository_rejects_non_string_alias_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"aliases": {"common": 123}})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with pytest.raises(ConfigError, match=r"`ansible\.aliases` must be a string map"):
        AliasRepository().entries()


def test_source_repository_rejects_non_list_source_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"sources": {"name": "prod"}})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with pytest.raises(ConfigError, match=r"`ansible\.sources` must be a list of mappings"):
        SourceRepository().entries()


def test_source_repository_reports_source_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"sources": [{"name": "prod"}]})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    with pytest.raises(
        ConfigError,
        match=r"invalid source 'prod': Value error, source requires --org, --team, or --repo",
    ):
        SourceRepository().entries()


def test_source_repository_upsert_replaces_existing_source_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"sources": [{"name": "prod", "repos": ["acme/old"]}]})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    SourceRepository().upsert(SourceDefinition(name="prod", repos=["acme/new"]))

    assert _read_state(cfg)["ansible"]["sources"] == [
        {
            "name": "prod",
            "repos": ["acme/new"],
            "orgs": [],
            "teams": [],
            "dependency_paths": [],
            "ref_kinds": [],
            "ref_patterns": [],
        }
    ]


def test_source_repository_remove_drops_empty_sources_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_config(tmp_path, {"sources": [{"name": "prod", "repos": ["acme/site"]}]})
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    assert SourceRepository().remove("prod")

    assert _read_state(cfg).get("ansible", {}).get("sources") is None
