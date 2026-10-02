"""Placements: every path an enabled item puts on this machine, resolved from the manifests.

A directory source is placed per child: the target directory stays a real
directory, each file under the source gets its own placement at the same
relative path, and files other programs write there are never touched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, q
from untaped_dotfiles.domain.manifest import FileEntry, Manifest, parse_manifest
from untaped_dotfiles.domain.models import (
    AppliedRecord,
    ItemChoice,
    Machine,
    MergeFormat,
    Mode,
    RepoRecord,
)
from untaped_dotfiles.domain.selection import effective_mode, selected_files
from untaped_dotfiles.errors import DotfilesError, ManifestError

if TYPE_CHECKING:
    from untaped_dotfiles.application.ports import DotfilesStore, GitRepos, SourceTree


@dataclass(frozen=True)
class Placement:
    """One path an enabled item places (a file entry, or one child of a directory entry)."""

    repo: RepoRecord
    choice: ItemChoice
    file: str
    """The manifest file entry's name (``FileEntry.key``)."""
    source: str
    target: Path
    mode: Mode
    fmt: MergeFormat | None = None
    executable: bool = False
    excluded: str | None = None
    orphan: bool = False
    """An applied record whose source the manifest or the tree no longer lists."""

    @property
    def item(self) -> str:
        return self.choice.name

    @property
    def key(self) -> str:
        return str(self.target)


@dataclass(frozen=True)
class Resolved:
    """The placements of some items, and the items that could not be resolved."""

    placements: list[Placement]
    problems: list[tuple[str, UntapedError]]
    """``(item id, error)`` for each enabled item whose repo or manifest entry is gone."""


class Inventory:
    """Reads manifests and expands enabled items into placements."""

    def __init__(
        self, store: DotfilesStore, git: GitRepos, *, home: Path, machine: Machine
    ) -> None:
        self._store = store
        self._git = git
        self._home = home.resolve()
        self._machine = machine

    @property
    def machine(self) -> Machine:
        return self._machine

    def source_tree(self, repo: RepoRecord) -> SourceTree:
        """Where ``copy``/``merge`` sources and the manifest are read.

        The fetched ref for a managed clone, the working tree for a registered checkout.
        """
        path = Path(repo.path)
        return self._git.ref_tree(path, repo.ref) if repo.managed else self._git.working_tree(path)

    def manifest(self, repo: RepoRecord) -> Manifest:
        tree = self.source_tree(repo)
        where = f"{repo.name}:{repo.manifest}"
        if not tree.exists(repo.manifest):
            raise ManifestError(f"manifest {where} not found", category="not_found")
        try:
            text = tree.read(repo.manifest).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManifestError(f"manifest {where} is not UTF-8") from exc
        return parse_manifest(text, where=where)

    def resolve_target(self, target: str) -> Path:
        """``target`` as an absolute path; ``~`` is this machine's home."""
        if target == "~":
            path = self._home
        elif target.startswith("~/"):
            path = self._home / target[2:]
        else:
            path = Path(target)
        path = Path(*path.parts)
        if path == self._home or path in self._home.parents:
            raise ManifestError(f"target {target!r} is the home directory or above it")
        return path

    def link_destination(self, repo: RepoRecord, source: str) -> Path:
        return Path(repo.path) / source

    def placements(
        self, choices: Sequence[ItemChoice], *, include_excluded: bool = False
    ) -> Resolved:
        """The placements of ``choices``, in manifest order, plus orphaned applied records."""
        found: list[Placement] = []
        problems: list[tuple[str, UntapedError]] = []
        for choice in choices:
            try:
                found.extend(self._item_placements(choice, include_excluded=include_excluded))
            except UntapedError as exc:
                problems.append((choice.id, exc))
        placed = {p.key for p in found}
        # an item that failed to resolve is reported, not treated as all orphans
        resolved = {c.id for c in choices} - {item for item, _ in problems}
        for record in self._store.applied():
            if f"{record.repo}/{record.item}" in resolved and record.target not in placed:
                found.append(self._orphan(record, choices))
        return Resolved(found, problems)

    def _item_placements(self, choice: ItemChoice, *, include_excluded: bool) -> list[Placement]:
        repo = self._store.get_repo(choice.repo)
        if repo is None:
            raise DotfilesError(
                f"repo {q(choice.repo)} is no longer subscribed",
                category="not_found",
                hint=f"run `untaped dotfiles disable {choice.name} --repo {choice.repo}`",
            )
        manifest = self.manifest(repo)
        item = manifest.items.get(choice.name)
        if item is None:
            raise DotfilesError(
                f"item {q(choice.name)} is no longer in the manifest of {q(repo.name)}",
                category="not_found",
                hint=f"run `untaped dotfiles disable {choice.name} --repo {repo.name}`",
            )
        tree = self.source_tree(repo)
        out: list[Placement] = []
        for entry, reason in selected_files(item, self._machine, skip=choice.skip):
            mode = effective_mode(entry.mode, choice.policy)
            base = Placement(
                repo=repo,
                choice=choice,
                file=entry.key,
                source=entry.source,
                target=self.resolve_target(entry.target),
                mode=mode,
                fmt=entry.merge_format,
            )
            if reason is not None:
                if include_excluded:
                    out.append(replace(base, excluded=reason))
                continue
            out.extend(self._expand(base, entry, tree))
        return out

    def _expand(self, base: Placement, entry: FileEntry, tree: SourceTree) -> list[Placement]:
        if not tree.is_dir(entry.source):
            return [base]
        if base.mode == "merge":
            raise ManifestError(f"merge file {q(entry.key)}: the source is a directory")
        prefix = entry.source + "/"
        return [
            replace(
                base,
                source=child.path,
                target=base.target / child.path.removeprefix(prefix),
                executable=child.executable,
            )
            for child in tree.files(entry.source)
        ]

    def _orphan(self, record: AppliedRecord, choices: Sequence[ItemChoice]) -> Placement:
        choice = next(c for c in choices if c.repo == record.repo and c.name == record.item)
        repo = self._store.get_repo(record.repo)
        assert repo is not None  # the item resolved above, so its repo exists
        fmt: MergeFormat | None = None
        if record.mode == "merge":
            fmt = "json" if record.target.endswith(".json") else "yaml"
        return Placement(
            repo=repo,
            choice=choice,
            file=record.file,
            source=record.source,
            target=Path(record.target),
            mode=record.mode,
            fmt=fmt,
            orphan=True,
        )
