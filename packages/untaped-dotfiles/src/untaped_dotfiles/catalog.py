"""The catalog: every package the subscribed sources offer, this machine's choices, and selectors.

A source's packages are the ``*.yml`` files directly under its committed
folder (``.untaped/dotfiles/``); each loads on its own, so one bad file is
one ``error`` row and the others still load. A selector names a package or
an item: ``PKG``, ``PKG/ITEM``, ``SOURCE/PKG`` or ``SOURCE/PKG/ITEM``, a
bare name while it is unique.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, UsageError, q
from untaped_dotfiles.errors import PackageError
from untaped_dotfiles.package import NAME, PackageFile, parse_package
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.settings import ItemChoice, PackageChoice

if TYPE_CHECKING:
    from untaped_dotfiles.models import Machine
    from untaped_dotfiles.sources import Source, Sources

_ON = ItemChoice()


@dataclass(frozen=True)
class Offer:
    """One package a source offers, as it loaded, and this machine's choice about it."""

    source: Source
    name: str
    package: PackageFile | None
    """``None`` when the file doesn't load (``error`` says why), or is gone (``gone``)."""
    error: UntapedError | None = None
    choice: PackageChoice | None = None
    """``None`` while the package is off on this machine."""
    gone: bool = False
    """The package is on here but its file left the source: what it placed is ``orphan``."""

    @property
    def on(self) -> bool:
        return self.choice is not None

    @property
    def spelled(self) -> str:
        """``SOURCE/PKG``: the spelling no other package shares."""
        return f"{self.source.name}/{self.name}"

    @property
    def policy(self) -> tuple[Policy | None, bool]:
        """``(policy, chosen)``: this machine's choice when it made one, else the author's."""
        if self.choice is not None and self.choice.policy is not None:
            return self.choice.policy, True
        if self.gone:
            return Policy.MANUAL, False
        return (self.package.policy if self.package is not None else None), False

    def item_choice(self, item: str) -> ItemChoice:
        """This machine's choice for ``item``: an item not listed is on."""
        if self.choice is None:
            return _ON
        return self.choice.items.get(item, _ON)

    def item_on(self, item: str) -> bool:
        """Whether ``item`` is on here: its package is on and the item isn't turned off."""
        return self.on and self.item_choice(item).enabled

    def item_policy(self, item: str) -> tuple[Policy | None, bool]:
        """``(policy, chosen)`` of ``item``: its own choice, else its package's."""
        own = self.item_choice(item).policy
        return (own, True) if own is not None else self.policy

    def exclusion(self, machine: Machine, item: str | None = None) -> str | None:
        """Why the package (or ``item``) doesn't apply on ``machine``, or ``None``."""
        if self.package is None:
            return None
        found = self.package.exclusion(machine.os, machine.tags)
        if found is None and item is not None and item in self.package.items:
            found = self.package.items[item].exclusion(machine.os, machine.tags)
        return found


@dataclass(frozen=True)
class Selection:
    """What a selector named: a package, or one item of it."""

    offer: Offer
    item: str | None = None

    @property
    def spelled(self) -> str:
        """``SOURCE/PKG`` or ``SOURCE/PKG/ITEM``."""
        return self.offer.spelled + (f"/{self.item}" if self.item else "")


def load_packages(
    source: Source, folder: str
) -> list[tuple[str, PackageFile | None, UntapedError | None]]:
    """``(name, package, error)`` per ``*.yml`` directly under ``folder`` in ``source``'s tree."""
    tree = source.tree
    if not tree.is_dir(folder):
        return []
    found: list[tuple[str, PackageFile | None, UntapedError | None]] = []
    for file in tree.files(folder):
        leaf = file.path.removeprefix(f"{folder}/")
        if "/" in leaf or not leaf.endswith(".yml"):
            continue
        name = leaf.removesuffix(".yml")
        where = f"{source.name}/{name}"
        try:
            if not NAME.match(name):
                raise PackageError(
                    f"package file {file.path}: the name {q(name)} must be lowercase letters, "
                    "digits, '_' or '-'"
                )
            try:
                text = tree.read(file.path).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackageError(f"package {where} is not UTF-8") from exc
            found.append((name, parse_package(text, where=where), None))
        except UntapedError as exc:
            found.append((name, None, exc))
    return found


class Catalog:
    """The packages of every subscribed source, loaded once per run, and selectors over them."""

    def __init__(self, sources: Sources, *, folder: str) -> None:
        self._sources = sources
        self._folder = folder
        self._offers: dict[str, list[Offer]] = {}
        self._problems: dict[str, UntapedError] = {}

    def offers(self, source: str | None = None) -> list[Offer]:
        """Every package of ``source`` (every source when ``None``), sorted by source and name.

        A source that can't be opened offers nothing; :attr:`problems` says why.
        """
        names = self._sources.names if source is None else [source]
        return [offer for name in names for offer in self._load(name)]

    @property
    def problems(self) -> dict[str, UntapedError]:
        """Each source that couldn't be opened, by name, after :meth:`offers` read it."""
        return dict(self._problems)

    def forget(self) -> None:
        """Read the sources again on the next ask (after a fetch or a move)."""
        self._offers.clear()
        self._problems.clear()
        self._sources.forget()

    def _load(self, name: str) -> list[Offer]:
        if name in self._offers:
            return self._offers[name]
        spec = self._sources.spec(name)
        try:
            source = self._sources.get(name)
            loaded = load_packages(source, self._folder)
        except UntapedError as exc:
            self._problems[name] = exc
            self._offers[name] = []
            return []
        names = {pkg for pkg, _, _ in loaded}
        gone = [
            Offer(source, pkg, None, choice=choice, gone=True)
            for pkg, choice in spec.packages.items()
            if pkg not in names
        ]
        offers = [
            Offer(source, pkg, package, error, spec.packages.get(pkg))
            for pkg, package, error in loaded
        ]
        self._offers[name] = sorted([*offers, *gone], key=lambda offer: offer.name)
        return self._offers[name]

    def select(self, selector: str) -> Selection:
        """What ``selector`` names; exit 2 when it names nothing, or two things."""
        parts = selector.split("/")
        if not 1 <= len(parts) <= 3 or not all(parts):
            raise UsageError(
                f"selector {q(selector)} must be PKG, PKG/ITEM, SOURCE/PKG or SOURCE/PKG/ITEM"
            )
        if len(parts) == 3:
            return self._full(*parts, selector=selector)
        if len(parts) == 1:
            return Selection(self._package(parts[0], selector=selector))
        first, second = parts
        as_package = self._find(first, second)
        as_item = self._items(first, second)
        if as_package is not None and as_item:
            items = ", ".join(each.spelled for each in as_item)
            alone = [offer for offer in self.offers() if offer.name == second]
            package_hint = (
                f", or the package by its name alone ({q(second)})"
                if len(alone) == 1
                else f"; package {as_package.spelled} has no other spelling while source "
                f"{q(first)} is named so"
            )
            raise UsageError(
                f"{q(selector)} names package {as_package.spelled} and item {items}",
                hint=f"name the item in full ({items}){package_hint}",
            )
        if as_package is not None:
            return Selection(as_package)
        if len(as_item) > 1:
            raise _ambiguous(f"item {q(selector)}", [each.spelled for each in as_item])
        if as_item:
            return as_item[0]
        if first in self._sources.names:
            raise UsageError(f"source {q(first)} has no package {q(second)}")
        offer = self._package(first, selector=selector)
        raise UsageError(f"package {offer.spelled} has no item {q(second)}")

    def _full(self, source: str, package: str, item: str, *, selector: str) -> Selection:
        offer = self._find(source, package)
        if offer is None:
            raise UsageError(f"no package {q(f'{source}/{package}')}")
        if offer.package is not None and item not in offer.package.items:
            raise UsageError(f"package {offer.spelled} has no item {q(item)}")
        return Selection(offer, item)

    def _find(self, source: str, package: str) -> Offer | None:
        if source not in self._sources.names:
            return None
        return next((o for o in self._load(source) if o.name == package), None)

    def _package(self, name: str, *, selector: str) -> Offer:
        found = [offer for offer in self.offers() if offer.name == name]
        if not found:
            raise UsageError(f"no subscribed source has a package {q(name)}")
        if len(found) > 1:
            raise _ambiguous(f"package {q(name)}", [offer.spelled for offer in found])
        return found[0]

    def _items(self, package: str, item: str) -> list[Selection]:
        return [
            Selection(offer, item)
            for offer in self.offers()
            if offer.name == package and offer.package is not None and item in offer.package.items
        ]

    def selected(self, selectors: list[str]) -> list[Selection]:
        """Each selector's selection, in order, each once; a package covers its items."""
        seen: dict[str, Selection] = {}
        for selector in selectors:
            found = self.select(selector)
            seen.setdefault(found.spelled, found)
        whole = {each.offer.spelled for each in seen.values() if each.item is None}
        return [
            each for each in seen.values() if each.item is None or each.offer.spelled not in whole
        ]


def _ambiguous(what: str, spellings: list[str]) -> UsageError:
    return UsageError(
        f"{what} is in {len(spellings)} sources: {', '.join(spellings)}",
        hint=f"name one: {', '.join(spellings)}",
    )
