"""recipe's ``setup migrate-dirs`` row moves the 10.x library into ``plugins/recipe/``."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import MigrationOptions, PluginContext
from untaped_recipe import SPEC
from untaped_recipe.settings import RecipeSettings

CTX, OPTIONS = PluginContext(settings=None), MigrationOptions()


def _old() -> Path:
    library = Path.home() / ".untaped" / "untaped-recipes"
    (library / "packs" / "base").mkdir(parents=True)
    (library / "packs.toml").write_text('[base]\nsource = "x"\n', encoding="utf-8")
    return library


def _new() -> Path:
    return Path.home() / ".untaped" / "plugins" / "recipe" / "library"


def test_the_10x_library_moves_whole() -> None:
    old = _old()
    (row,) = SPEC.migrations

    assert [(r.action, r.source, r.destination) for r in row.preview(CTX, OPTIONS)] == [
        ("move", str(old), str(_new()))
    ]
    assert [o.action for o in row.apply(CTX, OPTIONS)] == ["moved"]
    assert (_new() / "packs" / "base").is_dir() and (_new() / "packs.toml").is_file()
    assert not old.exists() and RecipeSettings().library_dir.expanduser() == _new()
    assert [o.action for o in row.apply(CTX, OPTIONS)] == ["unchanged"]


def test_a_library_a_command_made_with_nothing_in_it_is_replaced() -> None:
    old = _old()
    (_new() / "packs").mkdir(parents=True)
    (_new() / "packs.toml").write_text("", encoding="utf-8")
    (row,) = SPEC.migrations

    (move,) = row.preview(CTX, OPTIONS)
    assert move.action == "move" and "a command made before the move" in move.detail
    row.apply(CTX, OPTIONS)
    assert (_new() / "packs" / "base").is_dir() and not old.exists()


@pytest.mark.parametrize("data", ["packs/other/pyproject.toml", "backups/1/meta.json"])
def test_a_library_holding_a_pack_or_backup_is_never_replaced(data: str) -> None:
    old = _old()
    (_new() / data).parent.mkdir(parents=True)
    (_new() / data).write_text("x", encoding="utf-8")
    (row,) = SPEC.migrations

    (keep,) = row.preview(CTX, OPTIONS)
    assert keep.action == "keep" and "already exists" in keep.detail
    assert old.is_dir()


def test_a_profile_still_naming_the_old_library_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _old()
    monkeypatch.setenv("UNTAPED_RECIPE__LIBRARY_DIR", str(old))
    (row,) = SPEC.migrations

    (keep,) = row.preview(CTX, OPTIONS)
    assert keep.action == "keep" and "recipe.library_dir" in keep.detail
