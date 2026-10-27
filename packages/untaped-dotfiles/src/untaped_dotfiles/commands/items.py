"""``untaped dotfiles items|enable|disable``: the manifest items and this machine's choices."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from pydantic import Field

from untaped.sdk import Output, Positional, Short, plural, run_hint
from untaped_dotfiles.choices import DisableItems, EnableItems
from untaped_dotfiles.commands._dotfiles import REPO, Chosen, DotfilesCommand, utc_now
from untaped_dotfiles.models import item_id
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import Item, ItemOutcome


class Items(DotfilesCommand):
    """List manifest items with the suggested policy and this machine's choice.

    An item a manifest's `os`, `only` or `unless` filter leaves out on this
    machine says why in its `excluded` column.
    """

    repo: Annotated[str | None, Short("-r")] = Field(None, description=REPO)
    all_: bool = Field(False, description="Include items that do not apply on this machine.")
    output: Output[Item]

    def run(self) -> Iterator[Item]:
        self.output.empty("No items found.")
        return iter(self.services().catalog.rows(self.repo, include_excluded=self.all_))


class Enable(Chosen):
    """Enable items on this machine with a policy. Nothing is placed until `apply` or `sync`.

    Enabling again changes the policy or the skipped files and keeps the rest.
    """

    items: Positional[list[str]] = Field(default_factory=list, description="Item names to enable.")
    policy: Policy | None = Field(
        None,
        description="sync, once or manual. Default: the item's current policy when it is "
        "already enabled, else the manifest's suggestion (manual when it has none).",
    )
    skip: list[str] = Field(
        default_factory=list,
        description="File of the item to leave out on this machine, by its name (repeatable).",
    )
    output: Output[ItemOutcome]

    def run(self) -> Iterator[ItemOutcome]:
        names = self.names(self.items)
        svc = self.services()
        enable = EnableItems(svc.store, svc.catalog, now=utc_now)
        targets = enable.targets(names, repo=self.repo, every=self.all_)
        before = {choice.id for choice in svc.store.items()}
        rows = enable(targets, policy=self.policy, skip=self.skip)
        self.ctx.diag.success(f"enabled {plural(len(rows), 'item')}")
        self.ctx.diag.hint(run_hint("dotfiles apply"))
        return iter(
            [
                ItemOutcome(
                    repo=row.repo,
                    name=row.name,
                    action="updated" if item_id(row.repo, row.name) in before else "created",
                    policy=row.policy,
                    detail=f"skips {', '.join(row.skip)}" if row.skip else "",
                )
                for row in rows
            ]
        )


class Disable(Chosen):
    """Stop managing items on this machine. Placed files stay; `remove` deletes them."""

    items: Positional[list[str]] = Field(default_factory=list, description="Item names to disable.")
    output: Output[ItemOutcome]

    def run(self) -> Iterator[ItemOutcome]:
        names = self.names(self.items)
        choices = DisableItems(self.services().store)(names, repo=self.repo, every=self.all_)
        self.output.empty("No items to disable.")
        return iter(
            [
                ItemOutcome(repo=c.repo, name=c.name, action="deleted", policy=c.policy)
                for c in choices
            ]
        )
