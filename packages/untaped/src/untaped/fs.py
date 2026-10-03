"""Filesystem helpers for SDK commands: structured reads, atomic writes and file locks."""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

from untaped.errors import ConfigError, ErrorCategory, UntapedError


def atomic_write(
    path: Path,
    content: str | bytes,
    *,
    encoding: str = "utf-8",
    newline: str = "",
    mode: int | None = None,
) -> None:
    """Write ``content`` to ``path`` atomically and durably.

    The content goes to a temp file beside the target, is fsynced, then
    ``os.replace``-d over the target, and the directory is fsynced where the
    platform allows it; a failed write leaves the original untouched and no
    temp file behind. A symlinked ``path`` is written through: the link
    survives and the file it points at gets the content. An existing file
    keeps its permission bits; ``mode`` sets them instead (the temp file never
    has looser ones, even briefly); a new file otherwise gets the default mode
    (``0o666`` minus the umask). Creates ``path``'s missing parent
    directories, never those of a symlink's target: a link into a missing
    directory raises :class:`FileNotFoundError`. ``newline=""`` disables
    newline translation so the caller's line endings land on disk verbatim.
    ``bytes`` content is written as is (``encoding`` and ``newline`` then do
    nothing).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _write_target(path)
    tmp = _write_temp(target, content, encoding=encoding, newline=newline, mode=mode)
    try:
        _commit(tmp, target)
    finally:
        _remove_staged((tmp,))


def _write_target(path: Path) -> Path:
    """The file a write to ``path`` must replace: a symlink's final target."""
    return Path(os.path.realpath(path)) if path.is_symlink() else path


def _write_temp(
    target: Path,
    content: str | bytes,
    *,
    encoding: str = "utf-8",
    newline: str = "",
    mode: int | None = None,
) -> Path:
    """Write ``content`` to a fresh fsynced temp file beside ``target``; return it.

    The temp file gets ``mode``, else ``target``'s current mode, else the
    default mode for a new file.
    """
    if mode is None:
        with suppress(FileNotFoundError):
            mode = stat.S_IMODE(target.stat().st_mode)
    create_mode = 0o666 if mode is None else 0o600
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.untaped.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_mode)
        if mode is not None:
            os.chmod(fd, mode)
        if isinstance(content, str) and newline not in ("", "\n"):
            content = content.replace("\n", newline)
        data = content.encode(encoding) if isinstance(content, str) else content
        with open(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _remove_staged((tmp,))
        raise
    return tmp


def _commit(tmp: Path, target: Path) -> None:
    """Move a staged temp file over ``target`` and persist the rename."""
    os.replace(tmp, target)
    with suppress(OSError):  # directories cannot be opened or fsynced everywhere
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _remove_staged(paths: Iterable[Path]) -> None:
    for path in paths:
        with suppress(OSError):
            path.unlink(missing_ok=True)


@contextmanager
def file_lock(
    path: Path,
    *,
    timeout: float,
    error: Callable[[str], UntapedError],
    busy: str,
    failed: str,
) -> Iterator[None]:
    """Hold the advisory lock file ``path`` (its directory must exist) for the block.

    Waits up to ``timeout`` seconds for other processes holding it. If another
    process still holds it, raises ``error(busy)`` with category ``unavailable``
    (retrying later can succeed); if the lock file cannot be opened,
    ``error(f"{failed}: <reason>")``. Errors raised by the block itself pass
    through untouched.
    """
    lock = FileLock(str(path), timeout=timeout)
    try:
        lock.acquire()
    except TimeoutError as exc:  # filelock's ``Timeout``
        busy_error = error(busy)
        busy_error.category = ErrorCategory.UNAVAILABLE
        raise busy_error from exc
    except OSError as exc:
        raise error(f"{failed}: {exc.strerror or exc}") from exc
    try:
        yield
    finally:
        lock.release()


def read_structured_file(path: Path, *, flag: str | None = None) -> dict[str, Any]:
    """Read a YAML-or-JSON mapping file (``.json`` suffix → JSON parser).

    ``~`` is expanded and a blank document is an empty dict. ``flag`` names the
    CLI option the path came from (``--vars-file``) so messages read
    ``--vars-file file <path> …``. Raises :class:`ConfigError` when the file is
    missing or unreadable, does not parse, is not a mapping, or has non-string
    keys.
    """
    what = f"{flag} file" if flag else "file"
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"{what} not found: {path}", category="not_found") from exc
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or exc
        raise ConfigError(f"could not read {what} {path}: {reason}", category="invalid") from exc
    is_json = path.suffix.lower() == ".json"
    try:
        raw = (json.loads(text) if is_json else yaml.safe_load(text)) if text.strip() else None
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"{what} {path} is invalid {'JSON' if is_json else 'YAML'}: {exc}",
            category="invalid",
        ) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} {path} must contain a mapping", category="invalid")
    non_string = [repr(key) for key in raw if not isinstance(key, str)]
    if non_string:
        raise ConfigError(
            f"{what} {path}: keys must be strings (got {', '.join(non_string)})",
            category="invalid",
        )
    return raw
