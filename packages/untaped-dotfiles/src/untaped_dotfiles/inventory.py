"""Placements: every path an item that is on puts on this machine, resolved from its package.

A folder entry is placed per child: the target folder stays a real folder,
each file under the entry gets its own placement at the same relative path,
and files other programs write there are never touched. A target renders
``{{ name }}`` from this machine's answer (``inputs`` in the package's
choice), else the input's default; an input with neither leaves its
entries pending, naming the ``config set`` key that answers it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, key, q, run_hint
from untaped_dotfiles.errors import PackageError
from untaped_dotfiles.settings import DotfilesSettings
from untaped_values.api import InputError, placeholders, render

if TYPE_CHECKING:
    from untaped_dotfiles.catalog import Catalog, Offer, Selection
    from untaped_dotfiles.models import Machine, PlacedRecord
    from untaped_dotfiles.package import File, MergeFormat, Mode
    from untaped_dotfiles.policy import Policy
    from untaped_dotfiles.ports import DotfilesStore, SourceTree
    from untaped_dotfiles.sources import Source


@dataclass(frozen=True)
class Placement:
    """One path an item places (a file entry, or one child of a folder entry)."""

    offer: Offer
    item: str
    entry: str
    """The package file entry's ``path`` (a folder for its children)."""
    path: str
    """The source-relative path placed."""
    target: Path | None
    """``None`` while an input the target names has no value (``unanswered`` says which)."""
    shown: str
    """The target as the user reads it: ``~/...``, or the package's text while unrendered."""
    mode: Mode
    policy: Policy
    chosen: bool = False
    fmt: MergeFormat | None = None
    executable: bool = False
    excluded: str | None = None
    unanswered: str | None = None
    orphan: bool = False
    """A placed record the package or the tree no longer lists."""

    @property
    def source(self) -> Source:
        return self.offer.source

    @property
    def package(self) -> str:
        return self.offer.name

    @property
    def key(self) -> str:
        return str(self.target)


@dataclass(frozen=True)
class Resolved:
    """The placements of some packages, and the packages that could not be resolved."""

    placements: list[Placement]
    problems: list[tuple[str, UntapedError]]
    """``(SOURCE/PKG, error)`` for each package that doesn't load."""


class Inventory:
    """Expands the items that are on into placements."""

    def __init__(
        self, catalog: Catalog, store: DotfilesStore, *, home: Path, machine: Machine
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._home = home.resolve()
        self._machine = machine

    @property
    def machine(self) -> Machine:
        return self._machine

    @property
    def home(self) -> Path:
        return self._home

    def shown(self, target: Path) -> str:
        """``target`` as the user reads it: under ``~/`` when it is in the home folder."""
        try:
            return "~/" + target.relative_to(self._home).as_posix()
        except ValueError:
            return str(target)

    def resolve_target(self, target: str) -> Path:
        """``target`` as an absolute path; ``~`` is this machine's home."""
        if target == "~":
            path = self._home
        elif target.startswith("~/"):
            path = self._home / target[2:]
        else:
            path = Path(target)
        path = Path(*path.parts)
        if not path.is_absolute():
            raise PackageError(f"target {target!r} must start with '~/' or '/'")
        if path == self._home or path in self._home.parents:
            raise PackageError(f"target {target!r} is the home folder or above it")
        return path

    def link_destination(self, source: Source, path: str) -> Path:
        return source.root / path

    def on(self) -> list[Selection]:
        """Every package that is on, as a selection."""
        from untaped_dotfiles.catalog import Selection  # noqa: PLC0415 - a cycle

        return [Selection(offer) for offer in self._catalog.offers() if offer.on]

    def placements(
        self, selections: Sequence[Selection], *, include_excluded: bool = False
    ) -> Resolved:
        """The placements of ``selections`` (items that are on only), plus orphaned records."""
        found: list[Placement] = []
        problems: list[tuple[str, UntapedError]] = []
        resolved: set[tuple[str, str, str]] = set()
        offers: dict[tuple[str, str], Offer] = {}
        for selection in selections:
            offer = selection.offer
            if offer.package is None:
                assert offer.error is not None  # a package that doesn't load says why
                problems.append((offer.spelled, offer.error))
                continue
            offers[(offer.source.name, offer.name)] = offer
            items = [selection.item] if selection.item else list(offer.package.items)
            for item in items:
                if not offer.item_on(item):
                    continue
                try:
                    found.extend(self._item(offer, item, include_excluded=include_excluded))
                except UntapedError as exc:
                    problems.append((f"{offer.spelled}/{item}", exc))
                    continue
                resolved.add((offer.source.name, offer.name, item))
        placed = {p.key for p in found}
        for record in self._store.placed():
            owner = (record.source, record.package, record.item)
            if owner in resolved and record.target not in placed:
                found.append(self._orphan(record, offers[owner[:2]]))
        return Resolved(found, problems)

    def _item(self, offer: Offer, item: str, *, include_excluded: bool) -> list[Placement]:
        package = offer.package
        assert package is not None
        tree = offer.source.tree
        policy, chosen = offer.item_policy(item)
        assert policy is not None  # a package that loads has a policy
        out: list[Placement] = []
        for file in package.items[item].files:
            reason = offer.exclusion(self._machine, item) or file.exclusion(
                self._machine.os, self._machine.tags
            )
            if reason is not None and not include_excluded:
                continue
            base = self._placement(offer, item, file, policy=policy, chosen=chosen)
            if reason is not None:
                out.append(replace(base, excluded=reason))
            elif base.target is None:
                out.append(base)
            else:
                out.extend(self._expand(base, tree))
        return out

    def _placement(
        self, offer: Offer, item: str, file: File, *, policy: Policy, chosen: bool
    ) -> Placement:
        target, unanswered = self._render(offer, file.target)
        return Placement(
            offer=offer,
            item=item,
            entry=file.path,
            path=file.path,
            target=target,
            shown=self.shown(target) if target is not None else file.target,
            mode=file.mode,
            policy=policy,
            chosen=chosen,
            fmt=file.merge_format,
            unanswered=unanswered,
        )

    def _render(self, offer: Offer, text: str) -> tuple[Path | None, str | None]:
        """The target ``text`` names here, or ``(None, why)`` while an input has no value."""
        package = offer.package
        assert package is not None
        values = input_values(offer)
        missing = [name for name in placeholders(text, field="target") if name not in values]
        if missing:
            return None, unanswered(offer, missing)
        try:
            rendered = render(text, values, package.inputs, field="target")
        except InputError as exc:
            raise PackageError(f"{offer.spelled}: {exc}") from exc
        return self.resolve_target(rendered), None

    def _expand(self, base: Placement, tree: SourceTree) -> list[Placement]:
        if not tree.is_dir(base.entry):
            return [base]
        if base.mode == "merge":
            raise PackageError(f"merge file {q(base.entry)}: the source is a folder")
        assert base.target is not None
        prefix = base.entry + "/"
        return [
            replace(
                base,
                path=child.path,
                target=(target := base.target / child.path.removeprefix(prefix)),
                shown=self.shown(target),
                executable=child.executable,
            )
            for child in tree.files(base.entry)
        ]

    def _orphan(self, record: PlacedRecord, offer: Offer) -> Placement:
        policy, chosen = offer.item_policy(record.item)
        assert policy is not None
        target = Path(record.target)
        return Placement(
            offer=offer,
            item=record.item,
            entry=record.entry,
            path=record.path,
            target=target,
            shown=self.shown(target),
            mode=record.placed_as,
            policy=policy,
            chosen=chosen,
            fmt=record.fmt,
            orphan=True,
        )


def input_values(offer: Offer) -> dict[str, object]:
    """The package's top-level input values here: this machine's answers, else the defaults."""
    package = offer.package
    if package is None:
        return {}
    answers = offer.choice.inputs if offer.choice is not None else {}
    values: dict[str, object] = {}
    for name, spec in package.inputs.items():
        if spec.secret:
            continue
        if name in answers:
            values[name] = answers[name]
        elif spec.default is not None:
            values[name] = spec.default
    return values


def unanswered(offer: Offer, names: Iterable[str]) -> str:
    """``input 'x' has no value: `untaped config set ... VALUE```."""
    from untaped.core.api import Set  # noqa: PLC0415 - core's commands load only here

    named = list(names)
    setting = key(DotfilesSettings).sources[offer.source.name].packages[offer.name]
    hints = [run_hint(Set.spelled(setting.inputs[name], "VALUE")) for name in named]
    which = ", ".join(q(name) for name in named)
    return f"input {which} has no value: {'; '.join(hints)}"
