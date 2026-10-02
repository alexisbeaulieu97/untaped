"""Dotfiles port Protocols: git repos and source trees, the filesystem placer, and the store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from untaped_dotfiles.domain.models import (
        AppliedRecord,
        ItemChoice,
        KeyPath,
        MergeFormat,
        RepoRecord,
        TreeFile,
    )
    from untaped_dotfiles.domain.records import StatusSummary
    from untaped_dotfiles.domain.status import TargetInfo


class SourceTree(Protocol):
    """Files of a repo as read from one place: a fetched ref, or a working tree."""

    @property
    def commit(self) -> str | None:
        """The commit the tree is read from; ``None`` for a working tree."""
        ...

    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def files(self, path: str) -> list[TreeFile]:
        """Every file under directory ``path``, sorted by path (relative to the repo root)."""
        ...

    def read(self, path: str) -> bytes: ...


class GitRepos(Protocol):
    """Clones and checkouts of subscribed repos."""

    def clone(self, url: str, dest: Path, *, ref: str | None) -> str:
        """Clone ``url`` at ``dest`` on ``ref`` (the default branch when ``None``).

        Returns the branch name.
        """
        ...

    def fetch(self, path: Path) -> None: ...
    def head(self, path: Path) -> str: ...
    def is_clean(self, path: Path) -> bool: ...
    def behind(self, path: Path, ref: str) -> int:
        """Commits ``HEAD`` is behind ``origin/<ref>`` as last fetched."""
        ...

    def changed_paths(self, path: Path, ref: str, sources: Sequence[str]) -> set[str]:
        """Paths under ``sources`` that differ between ``HEAD`` and ``origin/<ref>``."""
        ...

    def fast_forward(self, path: Path, ref: str) -> bool:
        """Fast-forward the checkout to ``origin/<ref>``; whether it moved."""
        ...

    def ref_tree(self, path: Path, ref: str) -> SourceTree:
        """The files of ``origin/<ref>`` as last fetched."""
        ...

    def working_tree(self, path: Path) -> SourceTree: ...


class Placer(Protocol):
    """Places, inspects and removes paths on the machine."""

    def observe(
        self, target: Path, *, fmt: MergeFormat | None = None, managed: tuple[KeyPath, ...] = ()
    ) -> TargetInfo: ...

    def keep_aside(self, target: Path, *, repo: str, item: str, copy: bool = False) -> Path:
        """Move (or copy) what is at ``target`` into the kept directory; return where."""
        ...

    def link(self, destination: Path, target: Path) -> str:
        """Place a symlink to ``destination`` at ``target``; return the recorded target hash."""
        ...

    def copy(self, data: bytes, target: Path, *, executable: bool) -> str: ...
    def merge(
        self, source: bytes, target: Path, *, fmt: MergeFormat
    ) -> tuple[str, tuple[KeyPath, ...]]:
        """Merge the ``source`` document into ``target``.

        Returns the target hash and the managed key paths.
        """
        ...

    def parse(self, source: bytes, *, fmt: MergeFormat) -> dict[str, Any]: ...
    def preview_merge(self, source: bytes, target: Path, *, fmt: MergeFormat) -> str:
        """The text ``merge`` would write to ``target``, without writing it."""
        ...

    def unmerge(self, target: Path, *, fmt: MergeFormat, managed: tuple[KeyPath, ...]) -> None: ...
    def delete(self, target: Path) -> None: ...
    def render(self, target: Path, *, fmt: MergeFormat | None) -> str | None:
        """The target's text for a diff; ``None`` when it is missing."""
        ...


class DotfilesStore(Protocol):
    """Subscribed repos, item choices and applied records in ``state.yml``."""

    def repos(self) -> list[RepoRecord]: ...
    def get_repo(self, name: str) -> RepoRecord | None: ...
    def put_repo(self, record: RepoRecord) -> None: ...
    def remove_repo(self, name: str) -> bool: ...
    def items(self) -> list[ItemChoice]: ...
    def get_item(self, repo: str, name: str) -> ItemChoice | None: ...
    def put_item(self, choice: ItemChoice) -> None: ...
    def remove_item(self, repo: str, name: str) -> bool: ...
    def applied(self) -> list[AppliedRecord]: ...
    def put_applied(self, record: AppliedRecord) -> None: ...
    def remove_applied(self, target: str) -> bool: ...
    def locked(self) -> AbstractContextManager[None]:
        """Serialise ``apply``, ``sync`` and ``remove`` across processes."""
        ...

    def write_status(self, summary: StatusSummary) -> None:
        """Write ``status.json`` and the one-line ``attention`` file."""
        ...
