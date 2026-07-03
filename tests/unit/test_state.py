"""Behavioral tests for StateCollection/StateMap over the shared config file."""

from pathlib import Path

import pytest
import yaml

from untaped.errors import ConfigError
from untaped.state import StateCollection, StateMap


@pytest.fixture
def collection(_isolated_config: Path) -> StateCollection:
    return StateCollection("demo", "items")


def test_entries_empty_when_absent(collection: StateCollection) -> None:
    assert collection.entries() == []


def test_upsert_appends_then_replaces_by_id(collection: StateCollection) -> None:
    collection.upsert({"name": "a", "value": 1})
    collection.upsert({"name": "b", "value": 2})
    collection.upsert({"name": "a", "value": 9})
    assert collection.entries() == [{"name": "b", "value": 2}, {"name": "a", "value": 9}]


def test_insert_appends_but_rejects_duplicate_id(collection: StateCollection) -> None:
    collection.insert({"name": "a", "value": 1})

    with pytest.raises(ConfigError, match="already exists"):
        collection.insert({"name": "a", "value": 2})

    assert collection.entries() == [{"name": "a", "value": 1}]


def test_mutate_replaces_rows_and_returns_replacement(collection: StateCollection) -> None:
    collection.upsert({"name": "a", "value": 1})

    replacement = collection.mutate(lambda rows: [*rows, {"name": "b", "value": len(rows) + 1}])

    assert replacement == [{"name": "a", "value": 1}, {"name": "b", "value": 2}]
    assert collection.entries() == replacement


def test_get_returns_record_or_none(collection: StateCollection) -> None:
    collection.upsert({"name": "a", "value": 1})
    assert collection.get("a") == {"name": "a", "value": 1}
    assert collection.get("zzz") is None


def test_upsert_requires_the_id_field(collection: StateCollection) -> None:
    with pytest.raises(ConfigError, match="must include 'name'"):
        collection.upsert({"value": 1})


def test_remove_reports_and_collapses_empty_key(
    collection: StateCollection, _isolated_config: Path
) -> None:
    collection.upsert({"name": "a"})
    assert collection.remove("a") is True
    assert collection.remove("a") is False
    data = yaml.safe_load(_isolated_config.read_text(encoding="utf-8")) or {}
    assert "demo" not in data  # key collapsed → section collapsed


def test_entries_rejects_malformed_state(
    collection: StateCollection, _isolated_config: Path
) -> None:
    _isolated_config.write_text("demo:\n  items: not-a-list\n")
    with pytest.raises(ConfigError, match="must be a list"):
        collection.entries()


def test_state_map_set_get_remove(_isolated_config: Path) -> None:
    aliases = StateMap("demo", "aliases")
    assert aliases.entries() == {}
    aliases.set("web", "org/web-repo")
    assert aliases.get("web") == "org/web-repo"
    assert aliases.entries() == {"web": "org/web-repo"}
    assert aliases.remove("web") is True
    assert aliases.remove("web") is False
    data = yaml.safe_load(_isolated_config.read_text(encoding="utf-8")) or {}
    assert "demo" not in data


def test_state_map_rejects_malformed_mapping(_isolated_config: Path) -> None:
    _isolated_config.write_text("demo:\n  aliases: not-a-map\n")
    aliases = StateMap("demo", "aliases")

    with pytest.raises(ConfigError, match="must be a mapping"):
        aliases.entries()


def test_state_map_rejects_non_string_entries(_isolated_config: Path) -> None:
    _isolated_config.write_text("demo:\n  aliases:\n    web: 123\n")
    aliases = StateMap("demo", "aliases")

    with pytest.raises(ConfigError, match="must be a string map"):
        aliases.entries()


def test_state_map_set_does_not_replace_malformed_state(_isolated_config: Path) -> None:
    _isolated_config.write_text("demo:\n  aliases: not-a-map\n")
    aliases = StateMap("demo", "aliases")

    with pytest.raises(ConfigError, match="must be a mapping"):
        aliases.set("web", "org/web-repo")

    assert yaml.safe_load(_isolated_config.read_text(encoding="utf-8")) == {
        "demo": {"aliases": "not-a-map"}
    }
