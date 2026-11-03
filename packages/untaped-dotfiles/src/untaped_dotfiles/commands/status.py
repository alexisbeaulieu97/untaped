"""``untaped dotfiles status``: the state of every entry of the packages that are on, offline."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Matched, Output, Positional
from untaped_dotfiles.commands._dotfiles import DotfilesCommand
from untaped_dotfiles.records import EntryStatus


class Status(DotfilesCommand):
    """Show the state of every file entry of the packages that are on, offline.

    It fetches nothing, so `behind` is as of the last fetch. An entry that
    could not be read exits 1. Run without SELECTORS, it also rewrites
    status.json and attention. The policy guide (linked from the dotfiles
    README) explains each state.
    """

    selectors: Positional[list[str]] = Field(
        default_factory=list,
        description="Packages or items (PKG, PKG/ITEM, SOURCE/PKG, SOURCE/PKG/ITEM). "
        "Default: every package that is on.",
    )
    check: bool = Field(
        False,
        description="Exit 3 when an entry needs a person (behind, modified, conflict, missing "
        "or orphan).",
    )
    excluded: bool = Field(False, description="Include entries left out on this machine.")
    output: Output[EntryStatus]

    def run(self) -> Iterator[EntryStatus]:
        svc = self.services()
        selections = svc.catalog.selected(self.selectors) if self.selectors else svc.inventory.on()
        self.report_sources(svc.catalog)
        resolved = svc.inventory.placements(selections, include_excluded=self.excluded)
        self.report(resolved.problems)
        rows = svc.reader.rows(resolved.placements)
        summary = svc.reader.summary(rows)
        if not self.selectors:
            svc.store.write_status(summary)
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.package}/{row.item} {row.entry}")
        self.output.empty("No package is on.")
        yield from rows
        if self.check and summary.attention > 0:
            raise Matched
