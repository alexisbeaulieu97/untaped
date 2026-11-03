"""Dotfiles port Protocols: source trees, the filesystem placer, and the store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path

    from untaped_dotfiles.models import (
        CloneRecord,
        ItemRecord,
        KeyPath,
        PlacedRecord,
        StepRecord,
        TreeFile,
    )
    from untaped_dotfiles.package import MergeFormat
    from untaped_dotfiles.records import StatusSummary
    from untaped_dotfiles.states import TargetInfo


class SourceTree(Protocol):
    """A source's files as read from one place: a fetched commit, or a folder."""

    @property
    def commit(self) -> str | None:
        """The commit the tree is read from; ``None`` for a working tree."""
        ...

    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def files(self, path: str) -> list[TreeFile]:
        """Every file under folder ``path``, sorted by path (relative to the source's root)."""
        ...

    def read(self, path: str) -> bytes: ...


class Placer(Protocol):
    """Places, inspects and removes paths on the machine."""

    def observe(
        self, target: Path, *, fmt: MergeFormat | None = None, managed: tuple[KeyPath, ...] = ()
    ) -> TargetInfo: ...

    def keep_aside(self, target: Path, *, owner: tuple[str, ...], copy: bool = False) -> Path:
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

    def read(self, target: Path) -> bytes | None:
        """What a program reading ``target`` gets (through a link); ``None`` when no file."""
        ...

    def merged_text(self, source: bytes, target: Path, *, fmt: MergeFormat) -> str:
        """The text :meth:`merge` would write, writing nothing."""
        ...

    def parse(self, source: bytes, *, fmt: MergeFormat) -> dict[str, Any]: ...
    def unmerge(self, target: Path, *, fmt: MergeFormat, managed: tuple[KeyPath, ...]) -> None: ...
    def delete(self, target: Path) -> None: ...


class DotfilesStore(Protocol):
    """The clones untaped made and the paths it placed, in ``state.yml``."""

    def clones(self) -> list[CloneRecord]: ...
    def get_clone(self, source: str) -> CloneRecord | None: ...
    def put_clone(self, record: CloneRecord) -> None: ...
    def remove_clone(self, source: str) -> bool: ...
    def applied_item(self, owner: tuple[str, str, str]) -> ItemRecord | None:
        """Item ``(source, package, item)``'s row when it was applied here before."""
        ...

    def put_applied(self, owner: tuple[str, str, str], at: datetime) -> None: ...
    def note_waited(self, owner: tuple[str, str, str], entries: Iterable[tuple[str, str]]) -> None:
        """Add ``(entry, declared)`` pairs that waited on an input to an applied item's row."""
        ...

    def forget_applied(self, prefix: tuple[str, ...]) -> None: ...
    def placed(self) -> list[PlacedRecord]: ...
    def put_placed(self, record: PlacedRecord) -> None: ...
    def remove_placed(self, target: str) -> bool: ...
    def locked(self) -> AbstractContextManager[None]:
        """Serialise the commands that change files, clones or choices across processes."""
        ...

    def steps(self) -> list[StepRecord]: ...
    def put_step(self, record: StepRecord) -> None: ...
    def forget_steps(self, prefix: tuple[str, ...]) -> list[StepRecord]: ...
    def write_status(self, summary: StatusSummary) -> None:
        """Write ``status.json`` and the one-line ``attention`` file."""
        ...
