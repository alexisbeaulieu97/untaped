"""``FilesystemPlacer``: links, copies and merges on the machine, and the kept directory.

Before the tool replaces anything it did not write, the file moves to
``<kept_dir>/<repo>/<item>/<timestamp>/<path relative to home>``: a central
directory keeps the copy out of directories programs glob (``conf.d/*``),
which a ``<name>.before-dotfiles`` sibling would not. Nothing in it is ever
deleted by the tool.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from untaped.sdk import atomic_write
from untaped_dotfiles.domain.hashing import content_hash, value_hash
from untaped_dotfiles.domain.merge import leaf_paths, merge_into, project, remove_paths
from untaped_dotfiles.domain.models import KeyPath, MergeFormat
from untaped_dotfiles.domain.status import TargetInfo
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.infrastructure.documents import dump_document, load_document, plain


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
        if fmt is None:
            return TargetInfo(kind="file", hash=content_hash(target.read_bytes()))
        try:
            document = load_document(target.read_text(encoding="utf-8"), fmt=fmt, where=str(target))
        except DotfilesError, UnicodeDecodeError:
            return TargetInfo(kind="other")
        return TargetInfo(kind="file", hash=value_hash(project(plain(document), managed)))

    def keep_aside(self, target: Path, *, repo: str, item: str, copy: bool = False) -> Path:
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        try:
            relative = target.relative_to(self._home)
        except ValueError:
            relative = Path(*target.parts[1:])
        dest = self._kept_dir / repo / item / stamp / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(target, dest, follow_symlinks=False)
        else:
            shutil.move(str(target), str(dest))
        return dest

    def link(self, destination: Path, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        os.symlink(destination, target)
        return str(destination)  # what the ``link`` record stores as target_hash

    def copy(self, data: bytes, target: Path, *, executable: bool) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o755 if executable else 0o644)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return content_hash(data)

    def parse(self, source: bytes, *, fmt: MergeFormat) -> dict[str, Any]:
        document = load_document(source.decode("utf-8"), fmt=fmt, where="the merge source")
        result: dict[str, Any] = plain(document)
        return result

    def _merged(
        self, source: bytes, target: Path, *, fmt: MergeFormat
    ) -> tuple[str, MutableMapping[str, Any], dict[str, Any]]:
        """``(original text, merged document, incoming document)``."""
        incoming = self.parse(source, fmt=fmt)
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
        existing = load_document(text, fmt=fmt, where=str(target))
        merge_into(existing, incoming)
        return text, existing, incoming

    def preview_merge(self, source: bytes, target: Path, *, fmt: MergeFormat) -> str:
        text, existing, _ = self._merged(source, target, fmt=fmt)
        return dump_document(existing, fmt=fmt, like=text)

    def merge(
        self, source: bytes, target: Path, *, fmt: MergeFormat
    ) -> tuple[str, tuple[KeyPath, ...]]:
        text, existing, incoming = self._merged(source, target, fmt=fmt)
        atomic_write(target, dump_document(existing, fmt=fmt, like=text))
        managed = tuple(leaf_paths(incoming))
        return value_hash(project(plain(existing), managed)), managed

    def unmerge(self, target: Path, *, fmt: MergeFormat, managed: tuple[KeyPath, ...]) -> None:
        if not target.is_file():
            return
        text = target.read_text(encoding="utf-8")
        existing = load_document(text, fmt=fmt, where=str(target))
        remove_paths(existing, managed)
        atomic_write(target, dump_document(existing, fmt=fmt, like=text))

    def delete(self, target: Path) -> None:
        if target.is_symlink() or target.is_file():
            target.unlink()

    def render(self, target: Path) -> str | None:
        if target.is_symlink() or not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "<binary>\n"
