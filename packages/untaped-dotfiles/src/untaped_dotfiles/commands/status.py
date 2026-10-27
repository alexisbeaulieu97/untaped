"""``untaped dotfiles status|summary|diff``: the state of every placed path, read offline."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic import Field

from untaped.sdk import Matched, Output, unified_diff_text
from untaped_dotfiles.choices import pick_enabled
from untaped_dotfiles.commands._dotfiles import Selected, Services
from untaped_dotfiles.inventory import Placement
from untaped_dotfiles.records import PathStatus, StatusSummary


class _Paths(Selected):
    """The selected paths' status, read offline; unfiltered, it rewrites the status files."""

    all_: bool = Field(False, description="Include paths excluded on this machine.")

    def _status(self) -> tuple[list[PathStatus], StatusSummary]:
        svc = self.services()
        choices = pick_enabled(svc.store, self.items, repo=self.repo)
        resolved = svc.inventory.placements(choices, include_excluded=self.all_)
        self.report(resolved.problems)
        rows = svc.reader.rows(resolved.placements)
        summary = svc.reader.summary(rows, repos_behind=svc.reader.repos_behind())
        if not self.items and self.repo is None:
            svc.store.write_status(summary)
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.item}/{row.source}")
        return rows, summary


class Status(_Paths):
    """Show the state of every placed path, offline.

    It reads nothing from the network, so `behind` is as of the last `sync`
    or fetch. A path that could not be read exits 1. Run without ITEMS or
    --repo, it also rewrites status.json and attention. The policy guide
    (linked from the dotfiles README) explains each state.
    """

    check: bool = Field(
        False,
        description="Exit 3 when any path needs the user (behind, modified, conflict, missing "
        "or orphan).",
    )
    output: Output[PathStatus]

    def run(self) -> Iterator[PathStatus]:
        rows, summary = self._status()
        self.output.empty("No enabled items.")
        yield from rows
        if self.check and summary.attention > 0:
            raise Matched


class Summary(_Paths):
    """Print the counts over the placed paths: what status.json holds.

    Read offline, like `status`; `attention` counts the paths that make
    `status --check` and `sync` exit 3. Run without ITEMS or --repo, it also
    rewrites status.json and attention.
    """

    output: Output[StatusSummary]

    def run(self) -> StatusSummary:
        return self._status()[1]


class Diff(Selected):
    """Show what `apply` would change in copy and merge files (a unified diff on stdout).

    Reads managed clones as of the last fetch; `apply --dry-run` fetches first.
    """

    def run(self) -> str | None:
        svc = self.services()
        choices = pick_enabled(svc.store, self.items, repo=self.repo)
        resolved = svc.inventory.placements(choices)
        self.report(resolved.problems)
        text = "".join(self._diffs(svc, resolved.placements))
        return text.removesuffix("\n") or None

    def _diffs(self, svc: Services, placements: Sequence[Placement]) -> Iterator[str]:
        """The diff text of each copy and merge path that would change; a failure is reported."""
        svc.evaluator.prime_changes(placements)
        for p in placements:
            if p.mode == "link" or p.excluded:
                continue
            found = svc.evaluator.evaluate(p)
            if found.error is not None:
                self.ctx.diag.failure(found.error, item=f"{p.item}/{p.source}")
                continue
            if found.state not in ("pending", "foreign", "behind", "modified", "conflict"):
                continue
            before = svc.placer.render(p.target)
            data = svc.evaluator.tree(p.repo).read(p.source)
            if p.mode == "merge" and p.fmt is not None:
                after = svc.placer.preview_merge(data, p.target, fmt=p.fmt)
            else:
                after = data.decode("utf-8", errors="replace")
            yield unified_diff_text(before, after, path=svc.shown(p.target))
