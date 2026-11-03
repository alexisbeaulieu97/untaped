"""``untaped dotfiles status``: the state of every entry of the packages that are on, offline."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Matched, Output, Positional
from untaped_dotfiles.commands._dotfiles import DotfilesCommand
from untaped_dotfiles.records import EntryStatus


class Status(DotfilesCommand):
    """Show the state of every file entry of the packages that are on, offline.

    It fetches nothing, so `behind` is as of the last fetch. Without
    SELECTORS it also lists what untaped placed for a package or item that
    is off now (`off`), and rewrites status.json and attention. An entry
    that could not be read exits 1. The policy guide (linked from the
    dotfiles README) explains each state and what `apply` does with it.
    """

    selectors: Positional[list[str]] = Field(
        default_factory=list,
        description="Packages or items (PKG, PKG/ITEM, SOURCE/PKG, SOURCE/PKG/ITEM). "
        "Default: every package that is on.",
    )
    check: bool = Field(
        False,
        description="Exit 3 when there is something for `apply` or a person to do, by each "
        "entry's policy.",
    )
    excluded: bool = Field(False, description="Include entries left out on this machine.")
    output: Output[EntryStatus]

    def run(self) -> Iterator[EntryStatus]:
        svc = self.services()
        selections = svc.catalog.selected(self.selectors) if self.selectors else None
        resolved = svc.inventory.placements(selections, include_excluded=self.excluded)
        self.report_sources(svc.catalog)
        self.report(resolved.problems)
        reading = svc.reader.read(resolved.placements)
        rows = reading.rows
        problems = len(resolved.problems) + len(svc.catalog.problems)
        summary = svc.reader.summary(reading, problems=problems)
        if not self.selectors:
            svc.store.write_status(summary)
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.package}/{row.item} {row.entry}")
        self.output.empty("No package is on.")
        yield from rows
        if self.check and summary.attention + summary.apply > 0:
            raise Matched
