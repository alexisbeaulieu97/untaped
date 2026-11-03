"""``FilesystemPlacer``: links, copies and merges on the machine, and the kept directory.

Before untaped replaces anything it didn't write, the file moves to
``<kept_dir>/<source>/<package>/<item>/<timestamp>/<path relative to home>``: a central
directory keeps the copy out of directories programs glob (``conf.d/*``),
which a ``<name>.before-dotfiles`` sibling would not. Nothing in it is ever
deleted by untaped.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from untaped.sdk.storage import atomic_write
from untaped_dotfiles.documents import dump_document, load_document, load_values, merge_text, plain
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash, value_hash
from untaped_dotfiles.merge import leaf_paths, project, remove_paths
from untaped_dotfiles.models import KeyPath
from untaped_dotfiles.package import MergeFormat
from untaped_dotfiles.states import TargetInfo

_SOURCE = "the merge source"


class FilesystemPlacer:
    """:class:`Placer` over the real filesystem."""

    def __init__(
        self,
        *,
        home: Path,
        kept_dir: Path,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._home = home
        self._kept_dir = kept_dir
        self._now = now

    def observe(
        self, target: Path, *, fmt: MergeFormat | None = None, managed: tuple[KeyPath, ...] = ()
    ) -> TargetInfo:
        if target.is_symlink():
            return TargetInfo(kind="symlink", link_to=os.readlink(target))
        if not target.exists():
            return TargetInfo(kind="missing")
        if target.is_dir():
            return TargetInfo(kind="dir")
        if not target.is_file():
            return TargetInfo(kind="other")
        try:
            if fmt is None:
                return TargetInfo(kind="file", hash=content_hash(target.read_bytes()))
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return TargetInfo(kind="other")
        except OSError as exc:
            raise DotfilesError(f"could not read {target}: {exc.strerror or exc}") from exc
        try:
            document = load_document(text, fmt=fmt, where=str(target))
        except DotfilesError:
            return TargetInfo(kind="other")
        return TargetInfo(kind="file", hash=value_hash(project(plain(document), managed)))

    def keep_aside(self, target: Path, *, owner: tuple[str, ...], copy: bool = False) -> Path:
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        try:
            relative = target.relative_to(self._home)
        except ValueError:
            relative = Path(*target.parts[1:])
        dest = self._kept_dir.joinpath(*owner, stamp) / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(target, dest, follow_symlinks=False)
        else:
            shutil.move(str(target), str(dest))
        return dest

    def link(self, destination: Path, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            replace_symlink(destination, target)
        else:
            os.symlink(destination, target)
        return str(destination)  # what the ``link`` record stores as target_hash

    def copy(self, data: bytes, target: Path, *, executable: bool) -> str:
        if target.is_symlink():
            target.unlink()
        atomic_write(target, data, mode=(0o777 if executable else 0o666) & ~_umask())
        return content_hash(data)

    def parse(self, source: bytes, *, fmt: MergeFormat) -> dict[str, Any]:
        result: dict[str, Any] = plain(load_values(source.decode("utf-8"), fmt=fmt, where=_SOURCE))
        return result

    def merge(
        self, source: bytes, target: Path, *, fmt: MergeFormat
    ) -> tuple[str, tuple[KeyPath, ...]]:
        incoming = load_values(source.decode("utf-8"), fmt=fmt, where=_SOURCE)
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
        merged, rendered = merge_text(text, incoming, fmt=fmt, where=str(target))
        atomic_write(target, rendered)
        managed = tuple(leaf_paths(incoming))
        return value_hash(project(plain(merged), managed)), managed

    def unmerge(self, target: Path, *, fmt: MergeFormat, managed: tuple[KeyPath, ...]) -> None:
        if not target.is_file():
            return
        text = target.read_text(encoding="utf-8")
        existing = load_document(text, fmt=fmt, where=str(target))
        remove_paths(existing, managed)
        atomic_write(target, dump_document(existing, fmt=fmt, where=str(target), like=text))

    def delete(self, target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink()


def _umask() -> int:
    """The process umask (only readable by setting it; dotfiles runs single-threaded)."""
    current = os.umask(0o022)
    os.umask(current)
    return current


def replace_symlink(destination: Path, target: Path) -> None:
    """Point the symlink ``target`` at ``destination`` in one step (never missing meanwhile)."""
    # A short name: the target's own may already be near the filesystem's limit.
    digest = hashlib.sha256(os.fsencode(target.name)).hexdigest()[:12]
    temporary = target.with_name(f".untaped-relink-{digest}")
    temporary.unlink(missing_ok=True)
    os.symlink(destination, temporary)
    os.replace(temporary, target)
