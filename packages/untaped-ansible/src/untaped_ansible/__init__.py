"""Ansible plugin for the unified ``untaped`` shell.

GitHub behavior is consumed through the closed API in
:mod:`untaped_github.api`.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import (
    PluginContext,
    PluginSpec,
    SkillAsset,
    delete_migration,
    move_migration,
    old_dirs,
    plugin_dir,
    shown_path,
)
from untaped_ansible.doctor import DOCTOR_CHECKS
from untaped_ansible.settings import INDEX, AnsibleSettings, AnsibleState

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the ansible cyclopts app."""
    from untaped_ansible.cli import app  # noqa: PLC0415

    return app


#: 10.x's index, and the SQLite files beside it that travel with it (journal first).
_INDEX_10X = Path("~/.untaped/ansible-index.sqlite3")
_SIDECARS = ("-journal", "-wal", "-shm")


def _index_moves(ctx: PluginContext) -> Sequence[tuple[Path, Path]]:
    """10.x's index into ``plugin_dir`` while ``ansible.index_path`` keeps its default.

    A profile pointing ``index_path`` anywhere else (the old file included)
    keeps what it names; settings that don't validate move nothing.
    """
    settings = ctx.settings
    if not isinstance(settings, AnsibleSettings):
        return []
    if settings.index_path != AnsibleSettings.model_fields["index_path"].default:
        return []
    old, new = _INDEX_10X.expanduser(), plugin_dir(SPEC) / "index.sqlite3"
    pairs = [(old.with_name(old.name + end), new.with_name(new.name + end)) for end in _SIDECARS]
    return [*pairs, (old, new)]


def _git_cache_10x() -> Sequence[Path]:
    """10.x's git cache, with any custom root config.yml still names (9.x's key included)."""
    roots = old_dirs("~/.untaped/ansible-cache", "ansible", "cache_dir")
    return [*roots, *old_dirs("~/.untaped/ansible-cache", "ansible", "repo_cache_path")]


def _not_the_store(path: Path) -> str | None:
    """A custom root configured to ``git.store_dir``'s place is the store now: it stays."""
    from untaped_git.api import overlaps_store, store_root  # noqa: PLC0415

    if overlaps_store(path):
        return f"it overlaps the repo store ({shown_path(store_root())})"
    return None


def _git_cache_9x() -> Sequence[Path]:
    return [Path("~/.untaped/ansible-repositories").expanduser()]


SPEC = PluginSpec(
    name="ansible",
    app_factory=build_app,
    help="Analyze Ansible dependency graphs.",
    settings=AnsibleSettings,
    state=AnsibleState,
    skills=(
        SkillAsset(
            name="untaped-ansible",
            source=Path(str(files("untaped_ansible").joinpath("skills", "untaped-ansible"))),
            description=(
                "Maps Ansible role and project dependencies across GitHub repositories through "
                "the `untaped ansible` command (what a role depends on, what depends on it, which"
                " projects reach a repo, and dependency graphs). Use when the user asks who uses "
                "a role, the impact of changing one, or about requirements.yml or meta/main.yml "
                "dependencies."
            ),
        ),
    ),
    doctor_checks=DOCTOR_CHECKS,
    caches=(INDEX,),
    migrations=(
        move_migration(
            "ansible.index",
            "10.x dependency index",
            _index_moves,
            detail="saved sources keep their refreshed data",
        ),
        delete_migration(
            "ansible.cache",
            "10.x git cache",
            _git_cache_10x,
            detail="the index keeps each ref's commit: the next refresh fetches only what changed",
            guard=_not_the_store,
        ),
        delete_migration(
            "ansible.repositories",
            "9.x git cache",
            _git_cache_9x,
            detail="9.x layout",
            guard=_not_the_store,
        ),
    ),
)
