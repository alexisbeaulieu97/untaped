"""``items``, ``enable`` and ``disable``: the machine's choice about each manifest item."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from untaped.sdk import UsageError, not_found, q
from untaped_dotfiles.domain.manifest import Item
from untaped_dotfiles.domain.models import ItemChoice, Machine, Policy, RepoRecord
from untaped_dotfiles.domain.records import ItemRow
from untaped_dotfiles.domain.selection import item_exclusion
from untaped_dotfiles.errors import ItemNotFoundError, RepoNotFoundError

if TYPE_CHECKING:
    from untaped_dotfiles.application.inventory import Inventory
    from untaped_dotfiles.application.ports import DotfilesStore


class Catalog:
    """The manifest items of every subscribed repo, with the machine's choices."""

    def __init__(self, store: DotfilesStore, inventory: Inventory) -> None:
        self._store = store
        self._inventory = inventory

    @property
    def machine(self) -> Machine:
        return self._inventory.machine

    def repos(self, repo: str | None) -> list[RepoRecord]:
        found = self._store.repos()
        if repo is None:
            return found
        match = [r for r in found if r.name == repo]
        if not match:
            raise RepoNotFoundError(not_found("repo", repo, known=[r.name for r in found]))
        return match

    def entries(self, repo: str | None) -> list[tuple[RepoRecord, str, Item]]:
        """``(repo, item name, item)`` for every item of the selected repos, in manifest order."""
        out: list[tuple[RepoRecord, str, Item]] = []
        for record in self.repos(repo):
            manifest = self._inventory.manifest(record)
            out.extend((record, name, item) for name, item in manifest.items.items())
        return out

    def resolve(self, name: str, *, repo: str | None) -> tuple[RepoRecord, Item]:
        """The one item called ``name``.

        A name present in several repos without ``repo`` is a usage error.
        """
        matches = [(r, item) for r, found, item in self.entries(repo) if found == name]
        if not matches:
            known = sorted({found for _, found, _ in self.entries(repo)})
            raise ItemNotFoundError(not_found("item", name, known=known))
        if len(matches) > 1:
            repos = ", ".join(r.name for r, _ in matches)
            raise UsageError(
                f"item {q(name)} is in several repos: {repos}", hint="pass --repo NAME to pick one"
            )
        return matches[0]

    def row(self, repo: RepoRecord, name: str, item: Item) -> ItemRow:
        choice = self._store.get_item(repo.name, name)
        return ItemRow(
            repo=repo.name,
            name=name,
            suggested=item.policy,
            policy=choice.policy if choice else None,
            enabled=choice is not None,
            files=len(item.files),
            skip=choice.skip if choice else (),
            excluded=item_exclusion(item, self._inventory.machine),
            description=item.description,
        )

    def rows(self, repo: str | None, *, include_excluded: bool = True) -> list[ItemRow]:
        rows = [self.row(*entry) for entry in self.entries(repo)]
        return rows if include_excluded else [row for row in rows if row.excluded is None]


class EnableItems:
    """Record the machine's policy and skips for items (no placement happens here)."""

    def __init__(
        self, store: DotfilesStore, catalog: Catalog, *, now: Callable[[], datetime]
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._now = now

    def targets(
        self, names: Sequence[str], *, repo: str | None, every: bool
    ) -> list[tuple[RepoRecord, str, Item]]:
        if every:
            return [
                entry
                for entry in self._catalog.entries(repo)
                if item_exclusion(entry[2], self._catalog.machine) is None
            ]
        found: list[tuple[RepoRecord, str, Item]] = []
        for name in names:
            record, item = self._catalog.resolve(name, repo=repo)
            found.append((record, name, item))
        return found

    def __call__(
        self,
        targets: Sequence[tuple[RepoRecord, str, Item]],
        *,
        policy: Policy | None,
        skip: Sequence[str],
    ) -> list[ItemRow]:
        known = {entry.key for _, _, item in targets for entry in item.files}
        unknown = sorted(set(skip) - known)
        if unknown:
            named = ", ".join(q(name) for _, name, _ in targets)
            raise UsageError(
                f"no file named {', '.join(unknown)} in {named}",
                hint="file names are the `name` of a file entry, else its `source`",
            )
        rows: list[ItemRow] = []
        for record, name, item in targets:
            skipped = tuple(key for key in skip if any(e.key == key for e in item.files))
            existing = self._store.get_item(record.name, name)
            choice = ItemChoice(
                id=f"{record.name}/{name}",
                repo=record.name,
                name=name,
                policy=policy or (existing.policy if existing else item.policy),
                skip=skipped if skip or existing is None else existing.skip,
                enabled_at=existing.enabled_at if existing else self._now(),
            )
            self._store.put_item(choice)
            rows.append(self._catalog.row(record, name, item))
        return rows


class DisableItems:
    """Forget the machine's choice; placed files stay where they are."""

    def __init__(self, store: DotfilesStore) -> None:
        self._store = store

    def __call__(self, names: Sequence[str], *, repo: str | None, every: bool) -> list[ItemChoice]:
        choices = [c for c in self._store.items() if repo is None or c.repo == repo]
        if every:
            picked = choices
        else:
            picked = []
            for name in names:
                matches = [c for c in choices if c.name == name]
                if not matches:
                    raise ItemNotFoundError(
                        not_found("enabled item", name, known=sorted({c.name for c in choices}))
                    )
                if len(matches) > 1:
                    raise UsageError(
                        f"item {q(name)} is enabled from several repos: "
                        + ", ".join(c.repo for c in matches),
                        hint="pass --repo NAME to pick one",
                    )
                picked.extend(matches)
        for choice in picked:
            self._store.remove_item(choice.repo, choice.name)
        return picked
