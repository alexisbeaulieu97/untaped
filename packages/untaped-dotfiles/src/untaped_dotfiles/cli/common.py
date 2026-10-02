"""Shared dotfiles CLI plumbing: settings, the wired use cases, item selection and options."""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.sdk import ConfigError, UsageError, get_config_section, not_found, q
from untaped_dotfiles.application import (
    Applier,
    Catalog,
    Evaluator,
    Inventory,
    StatusReader,
)
from untaped_dotfiles.domain.models import ItemChoice, Machine, OsName
from untaped_dotfiles.errors import ItemNotFoundError
from untaped_dotfiles.infrastructure import FilesystemPlacer, LocalGitRepos, StateDotfilesStore
from untaped_dotfiles.settings import DotfilesSettings

ItemsArg = Annotated[
    list[str] | None,
    Parameter(name="ITEM", help="Item names; every enabled item when none is given."),
]
RepoOption = Annotated[
    str | None,
    Parameter(
        name=["--repo", "-r"],
        help="Limit to one subscribed repo (also picks an item name present in several).",
    ),
]
AllOption = Annotated[
    bool, Parameter(name="--all", negative="", help="Every item, not only the named ones.")
]

_OS: dict[str, OsName] = {"darwin": "macos", "linux": "linux", "windows": "windows"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def dotfiles_settings() -> DotfilesSettings:
    return get_config_section("dotfiles", DotfilesSettings)


def detect_os(settings: DotfilesSettings) -> OsName:
    if settings.os is not None:
        return settings.os
    found = _OS.get(platform.system().lower())
    if found is None:
        raise ConfigError(
            f"unknown operating system {platform.system()!r}",
            hint="run `untaped config set dotfiles.os linux` (or macos, windows)",
        )
    return found


@dataclass(frozen=True)
class Services:
    """Every use case, wired to the real adapters for the active profile."""

    settings: DotfilesSettings
    home: Path
    store: StateDotfilesStore
    git: LocalGitRepos
    placer: FilesystemPlacer
    inventory: Inventory
    evaluator: Evaluator
    reader: StatusReader
    applier: Applier
    catalog: Catalog

    @property
    def repos_dir(self) -> Path:
        return self.settings.repos_dir.expanduser().resolve()


def services() -> Services:
    settings = dotfiles_settings()
    home = Path.home().resolve()
    state_dir = settings.state_dir.expanduser().resolve()
    store = StateDotfilesStore(state_dir=state_dir)
    git = LocalGitRepos()
    placer = FilesystemPlacer(
        home=home, kept_dir=settings.kept_dir.expanduser().resolve(), now=utc_now
    )
    machine = Machine(os=detect_os(settings), tags=frozenset(settings.tags))
    inventory = Inventory(store, git, home=home, machine=machine)
    evaluator = Evaluator(store, git, placer, inventory)
    return Services(
        settings=settings,
        home=home,
        store=store,
        git=git,
        placer=placer,
        inventory=inventory,
        evaluator=evaluator,
        reader=StatusReader(store, git, evaluator, now=utc_now),
        applier=Applier(store, git, placer, inventory, evaluator, now=utc_now),
        catalog=Catalog(store, inventory),
    )


def enabled_choices(
    store: StateDotfilesStore, names: Sequence[str] | None, *, repo: str | None
) -> list[ItemChoice]:
    """The enabled items named (every enabled item without names), in state order."""
    choices = [c for c in store.items() if repo is None or c.repo == repo]
    if not names:
        return choices
    picked: list[ItemChoice] = []
    for name in names:
        matches = [c for c in choices if c.name == name]
        if not matches:
            raise ItemNotFoundError(
                not_found("enabled item", name, known=sorted({c.name for c in choices})),
                hint=f"run `untaped dotfiles enable {name}`",
            )
        if len(matches) > 1:
            raise UsageError(
                f"item {q(name)} is enabled from several repos: "
                + ", ".join(c.repo for c in matches),
                hint="pass --repo NAME to pick one",
            )
        picked.extend(matches)
    return picked
