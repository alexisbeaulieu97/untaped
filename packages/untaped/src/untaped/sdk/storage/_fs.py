"""Filesystem helpers for SDK commands: structured reads, atomic writes and file locks."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock

from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError


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


#: The end of a write's temp file name: ``.<name>.<32 hex digits>.untaped.tmp``.
_TEMP_SUFFIX = ".untaped.tmp"
_TEMP_NAME = re.compile(r"\..+\.[0-9a-f]{32}" + re.escape(_TEMP_SUFFIX))


def write_temp_name(target: Path) -> Path:
    """A fresh temp file name beside ``target``, one :func:`is_write_temp` recognizes."""
    return target.with_name(f".{target.name}.{uuid.uuid4().hex}{_TEMP_SUFFIX}")


def is_write_temp(name: str) -> bool:
    """Whether ``name`` is :func:`atomic_write`'s temp file: a write in flight, not data."""
    return _TEMP_NAME.fullmatch(name) is not None


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
    tmp = write_temp_name(target)
    try:
        text = isinstance(content, str)
        with open(
            tmp,
            "x" if text else "xb",
            encoding=encoding if text else None,
            newline=newline if text else None,
            opener=lambda name, flags: os.open(name, flags, create_mode),
        ) as handle:
            if mode is not None:
                _set_mode(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _remove_staged((tmp,))
        raise
    return tmp


def _set_mode(fd: int, mode: int) -> None:
    """Give the fresh temp file ``mode``; a filesystem that refuses modes keeps its own.

    Some CIFS and FUSE mounts refuse ``fchmod`` outright; the file was
    created no looser than ``0o600`` anyway.
    """
    try:
        os.chmod(fd, mode)
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise


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


#: The locks this process holds, by absolute path and thread: how many blocks hold each.
_HELD: dict[tuple[str, int], int] = {}
_HELD_GUARD = threading.Lock()


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
    through untouched. Re-entrant within a thread: taking a lock this thread
    already holds (a project's, around a step that takes it again) only counts,
    and the file is released when the outermost block ends.
    """
    key = (os.path.realpath(path), threading.get_ident())
    with _HELD_GUARD:
        held = key in _HELD
        if held:
            _HELD[key] += 1
    if held:
        try:
            yield
        finally:
            with _HELD_GUARD:
                _HELD[key] -= 1
        return
    lock = FileLock(str(path), timeout=timeout)
    try:
        lock.acquire()
    except TimeoutError as exc:  # filelock's ``Timeout``
        busy_error = error(busy)
        busy_error.category = ErrorCategory.UNAVAILABLE
        raise busy_error from exc
    except OSError as exc:
        raise error(f"{failed}: {exc.strerror or exc}") from exc
    with _HELD_GUARD:
        _HELD[key] = 1
    try:
        yield
    finally:
        with _HELD_GUARD:
            del _HELD[key]
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


#: The files each thread is writing (real paths), per thread like ``file_lock``'s
#: re-entrance: a write nested in another to the same file would be saved over
#: when the outer one saves. Keyed on the file, not its lock, which follows the
#: spelling the caller used.
_WRITING = threading.local()


@contextmanager
def locked_write(
    target: Path, *, timeout: float, busy: str, lock: Path | None = None
) -> Iterator[None]:
    """Hold the advisory ``<target>.lock`` (creating the parent directory) to write ``target``.

    ``lock`` puts the lock file elsewhere, for a file whose folder is another's.

    Waits up to ``timeout`` seconds for another process, then raises
    :class:`ConfigError` saying ``busy``. A write to ``target`` started inside
    another write to it in this thread raises :class:`UntapedError` and
    writes nothing: ``file_lock`` is re-entrant, so the inner write would
    succeed and the outer one would then save over it.
    """
    real = os.path.realpath(target)
    writing: frozenset[str] = getattr(_WRITING, "paths", frozenset())
    if real in writing:
        raise UntapedError(
            f"a write to {target} started inside another write to it; the inner "
            "write is refused so the outer one cannot save over it",
            details={"path": str(target)},
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"could not create the directory for {target}: {exc.strerror or exc}"
        ) from exc
    with file_lock(
        Path(f"{target}.lock") if lock is None else lock,
        timeout=timeout,
        error=ConfigError,
        busy=busy,
        failed=f"could not lock {target}",
    ):
        _WRITING.paths = writing | {real}
        try:
            yield
        finally:
            _WRITING.paths = writing
