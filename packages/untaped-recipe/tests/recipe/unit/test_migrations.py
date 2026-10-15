"""recipe's ``setup migrate-dirs`` row moves the 10.x library into ``plugins/recipe/``."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import MigrationOptions, PluginContext
from untaped_recipe import SPEC
from untaped_recipe.settings import RecipeSettings

OPTIONS = MigrationOptions()


def _old() -> Path:
    library = Path.home() / ".untaped" / "untaped-recipes"
    (library / "packs" / "base").mkdir(parents=True)
    (library / "packs.toml").write_text('[base]\nsource = "x"\n', encoding="utf-8")
    return library


def test_the_10x_library_moves_whole_while_the_setting_keeps_its_default() -> None:
    old = _old()
    new = Path.home() / ".untaped" / "plugins" / "recipe" / "library"
    ctx, (row,) = PluginContext(settings=RecipeSettings()), SPEC.migrations

    assert [(r.action, r.source, r.destination) for r in row.preview(ctx, OPTIONS)] == [
        ("move", str(old), str(new))
    ]
    assert [o.action for o in row.apply(ctx, OPTIONS)] == ["moved"]
    assert (new / "packs" / "base").is_dir() and (new / "packs.toml").is_file()
    assert not old.exists() and RecipeSettings().library_dir.expanduser() == new
    assert [o.action for o in row.apply(ctx, OPTIONS)] == ["unchanged"]


@pytest.mark.parametrize(
    "settings", [None, RecipeSettings(library_dir=Path("~/.untaped/untaped-recipes"))]
)
def test_a_profile_naming_a_library_or_unreadable_settings_move_nothing(
    settings: RecipeSettings | None,
) -> None:
    old = _old()
    (row,) = SPEC.migrations

    assert row.preview(PluginContext(settings=settings), OPTIONS) == []
    assert old.is_dir()
