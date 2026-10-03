"""Declared key renames: the rules they follow and the rewrite of one layer."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, Field

from untaped.deprecated_keys import KeyUse, key_mappings, mapping_errors, rename_keys
from untaped.errors import ConfigError


class Sweep(BaseModel):
    parallel: int = 12


class Section(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {
        "corpus_path": "cache_dir",
        "older_path": "corpus_path",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    retired_keys: ClassVar[dict[str, str]] = {"ancient_path": "older_path"}
    deprecated_settings: ClassVar[dict[str, str]] = {"legacy": "use mode: legacy is ignored"}

    cache_dir: str = "cache"
    legacy: bool = False
    symbols: dict[str, str] = Field(default_factory=dict)
    sweep: Sweep = Field(default_factory=Sweep)


def _model(**declarations: Any) -> type[BaseModel]:
    """A one-off section model with ``declarations`` as its ClassVars."""
    namespace: dict[str, Any] = {
        "__annotations__": {
            "cache_dir": str,
            "symbols": dict[str, str],
            "sweep": Sweep,
            **dict.fromkeys(declarations, ClassVar[dict[str, str]]),
        },
        "cache_dir": "cache",
        "symbols": Field(default_factory=dict),
        "sweep": Field(default_factory=Sweep),
        **declarations,
    }
    return type("Declared", (BaseModel,), namespace)


def test_chains_through_both_mappings() -> None:
    mappings = key_mappings(Section)

    assert mappings.readable == {
        "corpus_path": "cache_dir",
        "older_path": "cache_dir",
        "sweep.sync_concurrency": "sweep.parallel",
    }
    assert mappings.migratable["ancient_path"] == "cache_dir"
    assert mappings.retired == {"ancient_path"}
    assert mappings.distance == {
        "corpus_path": 1,
        "older_path": 2,
        "ancient_path": 3,
        "sweep.sync_concurrency": 1,
    }
    assert mappings.deprecated == {"legacy": "use mode: legacy is ignored"}


def test_a_model_without_declarations_maps_nothing() -> None:
    assert not key_mappings(Sweep)
    assert rename_keys(Sweep, {"parallel": 3}) == ({"parallel": 3}, ())


@pytest.mark.parametrize(
    ("declarations", "error"),
    [
        (
            {"renamed_keys": {"cache_dir": "symbols"}},
            "clashes with the current setting 'cache_dir'",
        ),
        (
            {"renamed_keys": {"sweep": "cache_dir"}},
            "clashes with the current setting 'sweep.parallel'",
        ),
        (
            {"renamed_keys": {"symbols.x": "cache_dir"}},
            "clashes with the current setting 'symbols'",
        ),
        ({"renamed_keys": {"old": "symbols.x"}}, "'symbols.x', which is not a setting"),
        ({"renamed_keys": {"old": "nowhere"}}, "'nowhere', which is not a setting"),
        ({"retired_keys": {"old": "nowhere"}}, "'nowhere', which is not a setting"),
        (
            {"renamed_keys": {"old": "gone"}, "retired_keys": {"gone": "cache_dir"}},
            "renamed key 'old' points at the retired key 'gone'",
        ),
        ({"renamed_keys": {"a": "b", "b": "a"}}, "the rename chain from 'a' is a cycle"),
        (
            {"renamed_keys": {"proxy": "cache_dir", "proxy.host": "cache_dir"}},
            "old key 'proxy.host' is below the old key 'proxy'",
        ),
        (
            {"renamed_keys": {"proxy.host": "cache_dir"}, "retired_keys": {"proxy": "cache_dir"}},
            "old key 'proxy.host' is below the old key 'proxy'",
        ),
        (
            {"renamed_keys": {"a": "cache_dir"}, "retired_keys": {"a": "cache_dir"}},
            "'a' is in both renamed_keys and retired_keys",
        ),
        ({"deprecated_settings": {"old": "use x"}}, "deprecated setting 'old' is not a setting"),
        (
            {"deprecated_settings": {"cache_dir": " "}},
            "deprecated setting 'cache_dir' needs a message",
        ),
        ({"renamed_keys": {"old": 3}}, "renamed_keys must map non-empty strings"),
    ],
)
def test_broken_declarations_are_reported(declarations: dict[str, Any], error: str) -> None:
    model = _model(**declarations)

    assert any(error in sentence for sentence in mapping_errors(model)), mapping_errors(model)
    with pytest.raises(ConfigError, match="invalid key declarations on Declared"):
        key_mappings(model)


def test_declarations_on_a_nested_model_are_reported() -> None:
    class Nested(BaseModel):
        renamed_keys: ClassVar[dict[str, str]] = {"old": "value"}
        value: int = 1

    class Outer(BaseModel):
        nested: Nested | None = None

    (error,) = mapping_errors(Outer)
    assert "declared on nested model Nested" in error
    with pytest.raises(ConfigError):
        key_mappings(Outer)


def test_old_key_is_read_as_the_new_one() -> None:
    data, uses = rename_keys(Section, {"corpus_path": "/c", "legacy": True})

    assert data == {"cache_dir": "/c", "legacy": True}
    assert uses == (
        KeyUse("corpus_path", "cache_dir", "renamed"),
        KeyUse("legacy", "legacy", "deprecated", message="use mode: legacy is ignored"),
    )


def test_the_new_key_wins_in_the_same_layer() -> None:
    data, uses = rename_keys(Section, {"cache_dir": "/new", "corpus_path": "/old"})

    assert data == {"cache_dir": "/new"}
    assert uses == (KeyUse("corpus_path", "cache_dir", "ignored", kept="cache_dir"),)


def test_the_closest_old_spelling_wins() -> None:
    data, uses = rename_keys(Section, {"older_path": "/older", "corpus_path": "/old"})

    assert data == {"cache_dir": "/old"}
    assert uses == (
        KeyUse("corpus_path", "cache_dir", "renamed"),
        KeyUse("older_path", "cache_dir", "ignored", kept="corpus_path"),
    )


def test_a_retired_key_is_reported_and_not_read() -> None:
    data, uses = rename_keys(Section, {"ancient_path": "/a"})

    assert data == {"ancient_path": "/a"}
    assert uses == (KeyUse("ancient_path", "cache_dir", "retired"),)


def test_nested_paths_move_and_empty_parents_go() -> None:
    raw = {"sweep": {"sync_concurrency": 4}}

    data, uses = rename_keys(Section, raw)

    assert data == {"sweep": {"parallel": 4}}
    assert uses == (KeyUse("sweep.sync_concurrency", "sweep.parallel", "renamed"),)
    assert raw == {"sweep": {"sync_concurrency": 4}}  # the input is not changed


def test_an_old_key_whose_new_parent_is_not_a_mapping_stays() -> None:
    model = _model(renamed_keys={"workers": "sweep.parallel"})

    data, uses = rename_keys(model, {"workers": 3, "sweep": 5})

    assert data == {"workers": 3, "sweep": 5}
    assert uses == ()
