"""dotfiles' ``setup migrate-dirs`` row: 10.x's ``~/.untaped/dotfiles`` into ``plugins/dotfiles``.

A 10.x machine is built with the real commands (the three directory settings
pointed at the old defaults), then the settings return to 11.0's defaults and
the migration runs: links still resolve, state names the new place, and
``status`` sees nothing to do.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from untaped.runtime.settings import get_settings
from untaped.sdk import MigrationOptions, PluginContext, StateCollection
from untaped.sdk.testing import CliInvoker
from untaped_dotfiles import SPEC
from untaped_dotfiles.cli import app

run = CliInvoker().invoke
OPTIONS = MigrationOptions()
Upstream = Callable[..., tuple[Path, Path]]


def _untaped() -> Path:
    return Path.home() / ".untaped"


@pytest.fixture
def ten_x(make_upstream: Upstream, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Subscribe and apply fish (links) and starship (a copy) in 10.x's layout; the link path."""
    old = _untaped() / "dotfiles"
    for key, value in (
        ("REPOS_DIR", old / "repos"),
        ("KEPT_DIR", old / "kept"),
        ("STATE_DIR", old),
    ):
        monkeypatch.setenv(f"UNTAPED_DOTFILES__{key}", str(value))
    monkeypatch.setenv("UNTAPED_DOTFILES__OS", "linux")
    get_settings.cache_clear()
    bare, _ = make_upstream()
    (home / ".config" / "starship.toml").parent.mkdir(parents=True)
    (home / ".config" / "starship.toml").write_text("mine\n")  # kept aside under kept/
    for args in (["subscribe", str(bare)], ["enable", "fish", "starship"], ["apply", "--yes"]):
        assert run(app, args).exit_code == 0
    for key in ("REPOS_DIR", "KEPT_DIR", "STATE_DIR"):
        monkeypatch.delenv(f"UNTAPED_DOTFILES__{key}")
    get_settings.cache_clear()
    link = home / ".config" / "fish" / "config.fish"
    assert os.readlink(link).startswith(str(old.resolve()))
    return link


def test_the_directory_moves_and_links_and_state_follow(ten_x: Path) -> None:
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]
    old, new = _untaped() / "dotfiles", _untaped() / "plugins" / "dotfiles"

    move, then = migration.preview(ctx, OPTIONS)
    assert (move.action, move.source, move.destination) == ("move", str(old), str(new))
    assert move.detail == "3 links repointed, 4 state records updated"
    assert then.action == "then" and "plugins/dotfiles/attention" in then.detail
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["moved"]

    assert not old.exists() and (new / "repos" / "dotfiles" / "dotfiles.yml").is_file()
    assert any((new / "kept").rglob("starship.toml"))
    assert os.readlink(ten_x) == str(new.resolve() / "repos" / "dotfiles" / "fish" / "config.fish")
    assert ten_x.read_text() == "set -gx EDITOR vim\n"
    (repo,) = StateCollection("dotfiles", "repos").entries()
    assert repo["path"] == str(new.resolve() / "repos" / "dotfiles")
    result = run(app, ["status", "--format", "json"])
    assert result.exit_code == 0 and '"applied"' in result.stdout, result.output
    assert migration.preview(ctx, OPTIONS) == []
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["unchanged"]


def test_a_crash_after_the_move_is_finished_by_the_next_run(ten_x: Path) -> None:
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]
    new = _untaped() / "plugins" / "dotfiles"
    new.parent.mkdir(parents=True)
    (_untaped() / "dotfiles").rename(new)  # moved, but nothing repointed yet

    (move,) = migration.preview(ctx, OPTIONS)
    assert move.action == "move" and move.detail == "3 links repointed, 4 state records updated"
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["moved"]
    assert ten_x.read_text() == "set -gx EDITOR vim\n"
    assert migration.preview(ctx, OPTIONS) == []


def test_a_profile_still_naming_the_old_directory_keeps_it(
    ten_x: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DOTFILES__KEPT_DIR", str(_untaped() / "dotfiles" / "kept"))
    (row,) = SPEC.migrations[0].preview(PluginContext(settings=None), OPTIONS)

    assert row.action == "keep" and "dotfiles.kept_dir" in row.detail
    assert os.readlink(ten_x).startswith(str((_untaped() / "dotfiles").resolve()))


def test_status_files_written_before_the_migration_are_replaced(ten_x: Path) -> None:
    assert run(app, ["status"]).exit_code == 0  # writes 11.0's status files and lock
    new = _untaped() / "plugins" / "dotfiles"
    assert (new / "attention").is_file()
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]

    assert [row.action for row in migration.preview(ctx, OPTIONS)] == ["move", "then"]
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["moved"]
    assert (new / "repos" / "dotfiles").is_dir() and ten_x.read_text() == "set -gx EDITOR vim\n"
    assert not (_untaped() / "dotfiles").exists()


def test_a_crash_part_way_through_the_entries_is_finished_by_the_next_run(ten_x: Path) -> None:
    new = _untaped() / "plugins" / "dotfiles"
    new.mkdir(parents=True)
    (_untaped() / "dotfiles" / "kept").rename(new / "kept")  # kept moved, repos not yet
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]

    assert [row.action for row in migration.preview(ctx, OPTIONS)] == ["move", "then"]
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["moved"]
    assert (new / "repos" / "dotfiles").is_dir() and any((new / "kept").rglob("starship.toml"))
    assert ten_x.read_text() == "set -gx EDITOR vim\n"


def test_an_entry_both_directories_hold_is_never_merged(ten_x: Path) -> None:
    (_untaped() / "plugins" / "dotfiles" / "repos").mkdir(parents=True)
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]

    (row,) = migration.preview(ctx, OPTIONS)
    assert row.action == "keep" and "plugins/dotfiles/repos already exists" in row.detail
    assert [o.action for o in migration.apply(ctx, OPTIONS)] == ["unchanged"]
    assert (_untaped() / "dotfiles" / "repos").is_dir()


def test_an_entry_made_while_waiting_for_the_lock_is_never_merged(
    ten_x: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped_dotfiles.infrastructure import migrations

    locked = migrations.StateDotfilesStore.locked
    made = _untaped() / "plugins" / "dotfiles" / "repos"

    @contextmanager
    def subscribed_meanwhile(self: migrations.StateDotfilesStore) -> Iterator[None]:
        with locked(self):
            made.mkdir(parents=True)  # a command that held the lock subscribed
            yield

    monkeypatch.setattr(migrations.StateDotfilesStore, "locked", subscribed_meanwhile)
    ctx, migration = PluginContext(settings=None), SPEC.migrations[0]

    (outcome,) = migration.apply(ctx, OPTIONS)
    assert outcome.action == "failed" and "plugins/dotfiles/repos already exists" in outcome.detail
    assert (_untaped() / "dotfiles" / "repos" / "dotfiles").is_dir() and not any(made.iterdir())


def test_apply_takes_the_lock_commands_take_under_a_custom_state_dir(
    ten_x: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped_dotfiles.infrastructure import migrations
    from untaped_dotfiles.settings import DotfilesSettings

    locked = migrations.StateDotfilesStore.locked
    taken: list[Path] = []

    @contextmanager
    def spy(self: migrations.StateDotfilesStore) -> Iterator[None]:
        taken.append(self._state_dir)
        with locked(self):
            yield

    monkeypatch.setattr(migrations.StateDotfilesStore, "locked", spy)
    state_dir = tmp_path / "custom-state"
    ctx = PluginContext(settings=DotfilesSettings(state_dir=state_dir))

    (outcome,) = SPEC.migrations[0].apply(ctx, OPTIONS)

    assert outcome.action == "moved", outcome.detail
    assert taken == [state_dir.resolve()]
