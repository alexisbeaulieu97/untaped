"""What every dotfiles command shares: the use cases wired for this run, and the summary rows."""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from untaped.sdk import Command, ConfigError, UntapedError, key, run_hint
from untaped_dotfiles.applier import Applier
from untaped_dotfiles.catalog import Catalog, Offer, gone_error
from untaped_dotfiles.inventory import Inventory
from untaped_dotfiles.models import Machine
from untaped_dotfiles.placer import FilesystemPlacer
from untaped_dotfiles.records import ApplyOutcome, Package
from untaped_dotfiles.settings import CLONES, KEPT, PROMPT, DotfilesSettings, Os
from untaped_dotfiles.sources import Clones, Sources
from untaped_dotfiles.status import Evaluator, StatusReader
from untaped_dotfiles.store import StateDotfilesStore

_OS: dict[str, Os] = {"darwin": "macos", "linux": "linux", "windows": "windows"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def detect_os(settings: DotfilesSettings) -> Os:
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

    home: Path
    store: StateDotfilesStore
    clones: Clones
    sources: Sources
    catalog: Catalog
    placer: FilesystemPlacer
    inventory: Inventory
    evaluator: Evaluator
    reader: StatusReader
    applier: Applier

    def refresh_status(self) -> None:
        """Rewrite status.json and attention after a change, so a prompt segment is current."""
        self.catalog.forget()
        self.evaluator.reset()
        resolved = self.inventory.placements()
        reading = self.reader.read(resolved.placements)
        problems = len(resolved.problems) + len(self.catalog.problems)
        self.store.write_status(self.reader.summary(reading, problems=problems))


class DotfilesCommand(Command[DotfilesSettings]):
    """A command over this machine's dotfiles, through the profile's ``dotfiles`` settings."""

    def services(self, settings: DotfilesSettings | None = None) -> Services:
        """The use cases, wired for this run (with ``settings`` when the command changed them)."""
        settings = settings or self.settings
        home = Path.home().resolve()
        store = StateDotfilesStore(state_dir=PROMPT.path.resolve())
        clones = Clones(
            store, plugin=self.ctx.plugin, clones_dir=CLONES.path.resolve(), now=utc_now
        )
        sources = Sources(settings.sources, store, clones)
        folder = self.ctx.plugin.committed("dotfiles").as_posix()
        catalog = Catalog(sources, folder=folder)
        placer = FilesystemPlacer(home=home, kept_dir=KEPT.path.resolve(), now=utc_now)
        machine = Machine(os=detect_os(settings), tags=frozenset(settings.tags))
        inventory = Inventory(catalog, store, home=home, machine=machine)
        evaluator = Evaluator(store, clones, placer, inventory)
        return Services(
            home=home,
            store=store,
            clones=clones,
            sources=sources,
            catalog=catalog,
            placer=placer,
            inventory=inventory,
            evaluator=evaluator,
            reader=StatusReader(evaluator, now=utc_now),
            applier=Applier(store, placer, inventory, evaluator, now=utc_now),
        )

    def report(self, problems: Sequence[tuple[str, UntapedError]]) -> None:
        """Each problem on stderr, named; each counts toward the exit code."""
        for item, exc in problems:
            self.ctx.diag.failure(exc, item=item)

    def report_sources(self, catalog: Catalog) -> None:
        """Each source that couldn't be opened, on stderr; each counts toward the exit code."""
        self.report(sorted(catalog.problems.items()))

    def report_rows(self, rows: Sequence[ApplyOutcome]) -> None:
        """Each row's error on stderr, named by its package, item and entry."""
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.package}/{row.item} {row.entry}")


def package_row(offer: Offer, machine: Machine) -> Package:
    """``offer`` as a ``Package`` row; a file that doesn't load carries its error."""
    from untaped.sdk import Failure  # noqa: PLC0415 - kept beside its only use

    policy, chosen = offer.policy
    package = offer.package
    error = gone_error(offer) if offer.gone else offer.error
    return Package(
        source=offer.source.name,
        name=offer.name,
        on=offer.on,
        policy=policy if offer.on else None,
        suggested=package.policy if package is not None else None,
        chosen=chosen,
        items=len(package.items) if package is not None else 0,
        description=package.description if package is not None else "",
        excluded=offer.exclusion(machine),
        error=Failure.from_exception(error, message=str(error)) if error is not None else None,
    )
