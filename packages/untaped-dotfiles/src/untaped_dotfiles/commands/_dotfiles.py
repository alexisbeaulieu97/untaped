"""What every dotfiles command shares: the use cases wired for this run, and the item selection."""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    Command,
    ConfigError,
    Positional,
    Short,
    UntapedError,
    UsageError,
    key,
    run_hint,
)
from untaped_dotfiles.applier import Applier
from untaped_dotfiles.choices import Catalog
from untaped_dotfiles.git_repos import LocalGitRepos
from untaped_dotfiles.inventory import Inventory
from untaped_dotfiles.models import Machine, OsName
from untaped_dotfiles.placer import FilesystemPlacer
from untaped_dotfiles.records import PlaceOutcome
from untaped_dotfiles.settings import DotfilesSettings
from untaped_dotfiles.status import Evaluator, StatusReader
from untaped_dotfiles.store import StateDotfilesStore

_OS: dict[str, OsName] = {"darwin": "macos", "linux": "linux", "windows": "windows"}

#: How ``--repo`` reads in help.
REPO = "Limit to one subscribed repo (also picks an item name present in several)."


def utc_now() -> datetime:
    return datetime.now(UTC)


def detect_os(settings: DotfilesSettings) -> OsName:
    if settings.os is not None:
        return settings.os
    found = _OS.get(platform.system().lower())
    if found is None:
        from untaped.core.api import Set  # noqa: PLC0415 - core's commands load only here

        raise ConfigError(
            f"unknown operating system {platform.system()!r}",
            hint=f"{run_hint(Set.spelled(key(DotfilesSettings).os, 'linux'))} (or macos, windows)",
        )
    return found


@dataclass(frozen=True)
class Services:
    """Every use case, wired to the real adapters for this run's settings."""

    repos_dir: Path
    home: Path
    store: StateDotfilesStore
    git: LocalGitRepos
    placer: FilesystemPlacer
    inventory: Inventory
    evaluator: Evaluator
    reader: StatusReader
    applier: Applier
    catalog: Catalog

    def shown(self, target: Path) -> str:
        """``target`` as the user reads it: under ``~/`` when it is in the home directory."""
        try:
            return "~/" + target.relative_to(self.home).as_posix()
        except ValueError:
            return str(target)


def services(settings: DotfilesSettings) -> Services:
    home = Path.home().resolve()
    store = StateDotfilesStore(state_dir=settings.state_dir.expanduser().resolve())
    git = LocalGitRepos()
    placer = FilesystemPlacer(
        home=home, kept_dir=settings.kept_dir.expanduser().resolve(), now=utc_now
    )
    machine = Machine(os=detect_os(settings), tags=frozenset(settings.tags))
    inventory = Inventory(store, git, home=home, machine=machine)
    evaluator = Evaluator(store, git, placer, inventory)
    return Services(
        repos_dir=settings.repos_dir.expanduser().resolve(),
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


class DotfilesCommand(Command[DotfilesSettings]):
    """A command over this machine's dotfiles, through the profile's ``dotfiles`` settings."""

    def services(self) -> Services:
        """The use cases, wired for this run."""
        return services(self.settings)

    def report(self, problems: Sequence[tuple[str, UntapedError]]) -> None:
        """Each item's problem on stderr; each counts toward the exit code."""
        for item, exc in problems:
            self.ctx.diag.failure(exc, item=item)

    def report_rows(self, rows: Sequence[PlaceOutcome]) -> None:
        """Each row's error on stderr, named by its item and source."""
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.item}/{row.source}")


class Selected(DotfilesCommand):
    """A command over enabled items: the named ones, or every one."""

    items: Positional[list[str]] = Field(
        default_factory=list, description="Item names; every enabled item when none is given."
    )
    repo: Annotated[str | None, Short("-r")] = Field(None, description=REPO)


class Chosen(DotfilesCommand):
    """A command over named items or ``--all``, never both or neither."""

    repo: Annotated[str | None, Short("-r")] = Field(None, description=REPO)
    all_: bool = Field(False, description="Every item, not only the named ones.")

    def names(self, given: list[str]) -> list[str]:
        """``given``, once checked against ``--all``."""
        if bool(given) == self.all_:
            raise UsageError("pass ITEMS or --all, not both or neither")
        return given
