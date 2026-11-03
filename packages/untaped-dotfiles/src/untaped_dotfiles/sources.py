"""Sources: where packages come from, and the clones untaped keeps of git sources.

A source is a git URL, a repo name a ``RepoSource`` provider lists, or a
local folder, told apart by how the argument starts (:func:`classify`), never
by what exists in the working directory. A git source is a detached worktree
of the git plugin's repo store under ``dotfiles.clones_dir`` (credentials and
proxy from whoever fills ``GitHost``); nobody works in it, and it moves to the
fetched commit when ``apply`` places. A local folder is read as it stands and
never fetched or moved, so links point into the author's own checkout.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from untaped.sdk import UntapedError, UsageError, q, run_hint
from untaped_dotfiles.errors import DotfilesError, GitError
from untaped_dotfiles.models import CloneRecord
from untaped_dotfiles.package import NAME
from untaped_dotfiles.trees import StoreTree, WorkingTree
from untaped_git.api import RepoFinder, RepoStore, default_branch, looks_like_url, run_git

if TYPE_CHECKING:
    from untaped_dotfiles.ports import DotfilesStore, SourceTree
    from untaped_dotfiles.settings import PackageSource

SourceKind = Literal["folder", "url", "name"]

#: Seconds a local git read in a clone may take.
_TIMEOUT = 60.0

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def classify(arg: str) -> SourceKind:
    """What ``arg`` names: a local folder, a git URL, or a repo name, by how it starts.

    A folder starts with ``/``, ``~``, ``.`` or a drive (``C:\\``); a URL is
    what the git plugin reads as one (``://``, scp-like ``git@host:path``, or
    ending in ``.git``); anything else is a repo name, even when a folder of
    that name exists here (spell it ``./owner/repo``).
    """
    if arg.startswith(("/", "~", ".")) or _DRIVE.match(arg):
        return "folder"
    return "url" if looks_like_url(arg) else "name"


def name_from(arg: str) -> str:
    """A source's default name: the last segment of its URL, name or folder, without ``.git``."""
    leaf = arg.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return leaf.removesuffix(".git").lower()


def check_name(name: str) -> str:
    """``name`` when it can be a source's name (one segment of a config key)."""
    if not NAME.match(name):
        raise UsageError(
            f"source name {q(name)} must be lowercase letters, digits, '_' or '-'",
            hint="pass --name",
        )
    return name


def resolve_url(arg: str) -> str:
    """The clone URL ``arg`` names, through the git plugin: a typed URL as it is (refusing what
    git refuses), a name as a repo source lists it."""
    return RepoFinder().resolve(arg).url


def current(record: CloneRecord | None, spec: PackageSource) -> bool:
    """Whether ``record`` is the clone ``spec`` asks for: same URL setting and ref, still there."""
    return stale(record, spec) is None


def stale(record: CloneRecord | None, spec: PackageSource) -> str | None:
    """Why ``record`` is not the clone ``spec`` asks for, or ``None`` when it is."""
    if record is None:
        return "has no clone yet"
    if record.given != spec.url or (spec.ref or record.ref) != record.ref:
        return "changed in config.yml since it was cloned"
    if not Path(record.folder).is_dir():
        return f"lost its clone at {record.folder}"
    return None


@dataclass(frozen=True)
class Source:
    """One subscribed source as this run reads it."""

    name: str
    spec: PackageSource
    root: Path
    """Where links point: the clone's folder, or the local folder."""
    tree: SourceTree
    """Where packages, ``copy`` and ``merge`` files are read."""
    clone: CloneRecord | None = None
    """The clone untaped keeps; ``None`` for a local folder."""
    peeked: bool = False
    """A git source read from the fetch with no clone yet (a preview): links read the tree."""


class Clones:
    """The git plugin's store repos and worktrees behind git sources."""

    def __init__(
        self,
        store: DotfilesStore,
        *,
        plugin: object,
        clones_dir: Path,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._plugin = plugin
        self._clones_dir = clones_dir
        self._now = now

    def repo(self, url: str) -> RepoStore:
        return RepoStore.for_url(url, plugin=self._plugin, error=GitError)

    def folder(self, source: str) -> Path:
        return self._clones_dir / source

    def fetched(self, url: str, ref: str | None) -> str:
        """Fetch ``url``'s branch ``ref`` (its default branch when ``None``) into the store.

        Returns the branch; one the remote doesn't have is not found.
        """
        branch = ref or default_branch(url)
        if branch is None:
            raise GitError(f"{url} has no default branch", category="not_found", hint="pass --ref")
        repo = self.repo(url)
        repo.fetch(branches=[branch])
        if f"heads/{branch}" not in repo.refs():
            raise GitError(
                f"{url} has no branch {q(branch)}", category="not_found", hint="pass --ref"
            )
        return branch

    def peek(self, url: str, ref: str | None) -> tuple[StoreTree, str, Callable[[], None]]:
        """``url`` at ``ref`` read from the store with no clone: the tree, its branch, and a
        callback that lets the store repo go again when it wasn't held before."""
        repo = self.repo(url)
        held = repo.has_part()
        branch = self.fetched(url, ref)
        tree = StoreTree(repo, self._commit(repo, branch))

        def done() -> None:
            if not held:
                repo.release()

        return tree, branch, done

    def make(self, source: str, given: str, url: str, *, ref: str | None) -> CloneRecord:
        """Fetch ``url`` at ``ref`` (its default branch when ``None``) and add the detached clone.

        The clone goes to ``clones_dir/<source>``, which must not exist.
        """
        folder = self.folder(source)
        if folder.exists():
            raise DotfilesError(
                f"{folder} already exists",
                category="conflict",
                hint="move it away, or subscribe under another --name",
            )
        branch = self.fetched(url, ref)
        repo = self.repo(url)
        folder.parent.mkdir(parents=True, exist_ok=True)
        repo.worktree_add(folder, repo.ref(f"heads/{branch}"))
        return CloneRecord(
            source=source,
            given=given,
            url=url,
            ref=branch,
            folder=str(folder),
            subscribed_at=self._now(),
        )

    @staticmethod
    def _commit(repo: RepoStore, branch: str) -> str:
        ref = repo.ref(f"heads/{branch}")
        return repo.run(["rev-parse", "--verify", f"{ref}^{{commit}}"], capture=True).text.strip()

    def head(self, record: CloneRecord) -> str:
        """The commit the source's branch was at when last fetched."""
        return self._commit(self.repo(record.url), record.ref)

    def checked_out(self, record: CloneRecord) -> str | None:
        """The commit the clone has checked out; ``None`` when it can't be read."""
        try:
            return run_git(
                ["rev-parse", "HEAD"], cwd=Path(record.folder), capture=True, timeout=_TIMEOUT
            ).text.strip()
        except UntapedError:
            return None

    def tree(self, record: CloneRecord) -> StoreTree:
        return StoreTree(self.repo(record.url), self.head(record))

    def checked_out_tree(self, record: CloneRecord) -> StoreTree | None:
        """The tree of the commit the clone has checked out (without its local changes)."""
        commit = self.checked_out(record)
        return StoreTree(self.repo(record.url), commit) if commit is not None else None

    def fetch(self, record: CloneRecord) -> None:
        self.repo(record.url).fetch(branches=[record.ref])

    def changed(self, record: CloneRecord, paths: Sequence[str]) -> set[str]:
        """Paths under ``paths`` the fetched commit changed since the one checked out."""
        here = self.checked_out(record)
        if not paths or here is None:
            return set()
        head = self.head(record)
        if head == here:
            return set()
        out = self.repo(record.url).run(
            ["diff", "--name-only", "-z", here, head, "--", *paths], capture=True
        )
        return {name for name in out.text.split("\0") if name}

    def behind(self, record: CloneRecord) -> bool:
        """Whether the fetched commit is not the one the clone has checked out."""
        return self.checked_out(record) != self.head(record)

    def local_changes(self, record: CloneRecord) -> GitError | None:
        """Why the clone can't be moved or made again: it has local changes. ``None`` when clean
        or gone."""
        folder = Path(record.folder)
        if not folder.is_dir():
            return None
        changes = run_git(
            ["status", "--porcelain"], cwd=folder, capture=True, timeout=_TIMEOUT
        ).text.strip()
        if not changes:
            return None
        from untaped_dotfiles.commands.sources import Subscribe  # noqa: PLC0415 - a cycle

        return GitError(
            f"the clone of {q(record.source)} has local changes, so it stays where it is",
            category="conflict",
            hint=f"edit in your own checkout ({run_hint(Subscribe.spelled('PATH'))}), or "
            f"discard them: git -C {folder} checkout -- .",
        )

    def move(self, record: CloneRecord) -> None:
        """Check the fetched commit out in the clone; refused while the clone has local changes.

        An edit to a linked file lands in the clone; it is never overwritten.
        """
        stuck = self.local_changes(record)
        if stuck is not None:
            raise stuck
        repo = self.repo(record.url)
        repo.checkout(Path(record.folder), repo.ref(f"heads/{record.ref}"))

    def ensure(self, name: str, spec: PackageSource) -> tuple[CloneRecord, bool]:
        """Source ``name``'s clone, made again when it is missing or its URL or ref changed.

        Returns the clone and whether it was made now.
        """
        record = self._store.get_clone(name)
        if current(record, spec):
            assert record is not None
            return record, False
        assert spec.url is not None
        if record is not None:
            stuck = self.local_changes(record)
            if stuck is not None:
                raise stuck
            self.release(record)
            self._store.remove_clone(name)
        record = self.make(name, spec.url, resolve_url(spec.url), ref=spec.ref)
        self._store.put_clone(record)
        return record, True

    def release(self, record: CloneRecord) -> None:
        """Let go of the clone's worktree and folder, and of the store repo when no other source
        clones the same URL."""
        repo = self.repo(record.url)
        shared = any(
            other.source != record.source and self.repo(other.url).path == repo.path
            for other in self._store.clones()
        )
        try:
            if shared:
                repo.run(["worktree", "remove", "--force", "--force", record.folder], check=False)
            else:
                repo.release()
        finally:
            shutil.rmtree(record.folder, ignore_errors=True)


class Sources:
    """The subscribed sources of this run's settings, opened on demand."""

    def __init__(
        self, specs: dict[str, PackageSource], store: DotfilesStore, clones: Clones
    ) -> None:
        self._specs = specs
        self._store = store
        self._clones = clones
        self._open: dict[str, Source] = {}
        self._peeked: dict[str, Source] = {}

    @property
    def names(self) -> list[str]:
        return sorted(self._specs)

    @property
    def specs(self) -> dict[str, PackageSource]:
        return dict(self._specs)

    def spec(self, name: str) -> PackageSource:
        return self._specs[name]

    def get(self, name: str) -> Source:
        """Source ``name`` opened: its clone's store tree, or its folder."""
        if name not in self._open:
            self._open[name] = self._opened(name, self._specs[name])
        return self._open[name]

    def forget(self) -> None:
        """Drop the opened sources (after a fetch or a move); a peeked one stays."""
        self._open.clear()

    def peek(self, name: str, tree: SourceTree) -> None:
        """Read git source ``name`` from ``tree`` this run, as if cloned: a preview's view of
        a source with no clone yet (its links would point into ``clones_dir/<name>``)."""
        spec = self._specs[name]
        self._peeked[name] = Source(
            name=name, spec=spec, root=self._clones.folder(name), tree=tree, peeked=True
        )

    def _opened(self, name: str, spec: PackageSource) -> Source:
        if name in self._peeked:
            return self._peeked[name]
        if spec.path is not None:
            root = Path(spec.path).expanduser()
            if not root.is_dir():
                raise DotfilesError(
                    f"source {q(name)}: the folder {root} is gone", category="not_found"
                )
            return Source(name=name, spec=spec, root=root.resolve(), tree=WorkingTree(root))
        record = self._store.get_clone(name)
        why = stale(record, spec)
        if why is not None:
            from untaped_dotfiles.commands.apply import Apply  # noqa: PLC0415 - a cycle

            raise DotfilesError(
                f"source {q(name)} {why}",
                category="not_found",
                hint=f"{run_hint(Apply.spelled())} clones it",
            )
        assert record is not None
        return Source(
            name=name,
            spec=spec,
            root=Path(record.folder),
            tree=self._clones.tree(record),
            clone=record,
        )
