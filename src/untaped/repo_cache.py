"""Shared bare-repository cache: path scheme, listing, and one git-backed handle.

Layout: ``<root>/<host>/<every URL path segment>.git`` (nested groups
included), so the https and ssh URLs of one repo share one cache, and
``<root>/_unknown/<hash>.git`` for URLs without a host (local paths,
``file://``). Every part is one ``safe_path_segment``, so a hostile URL never
leaves the root.

:class:`RepoCache` is one such bare repository. Its lock is the sibling file
``<cache>.lock`` (via ``file_lock``), so two untaped processes on one cache
serialize and the lock is never inside the repository. Every git call on it
goes through ``run_git`` and carries the auth header only when the cache's
origin is an ``https://`` URL on the trusted ``auth_host``; ssh, ``file``
and other-host origins never see it.

The cache owns mechanics only. Each capability keeps its own root, its ref
policy (what to fetch, what to prune) and its error mapping.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from untaped.errors import UntapedError, attribution
from untaped.fs import file_lock
from untaped.git import GitCommandError, GitResult, run_git, safe_path_segment

_SCP = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")
_ORIGIN_SECTION = re.compile(r'\[\s*(?i:remote)\s+"origin"\s*\]')
_ESCAPES = {"n": "\n", "t": "\t", "b": "\b"}
_UNKNOWN = "_unknown"


def repo_url_parts(url: str) -> tuple[str | None, list[str]]:
    """``(host, path segments with the last .git removed)`` of a URL, ``user@host:path`` or path.

    The host is lowercased, as ``urlparse`` does. A plain path or ``file://``
    URL has no host.
    """
    match = _SCP.match(url)
    if "://" in url:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path or ""
    elif match:
        host, path = match.group("host").lower(), match.group("path")
    else:
        host, path = None, url
    segments = [s for s in path.replace("\\", "/").split("/") if s]
    if segments:
        segments[-1] = segments[-1].removesuffix(".git")
    return host, segments


def cache_key(url: str) -> tuple[str, ...]:
    """``(<host>, <segment>..., <name>.git)``; ``("_unknown", "<sha256[:16]>.git")`` without a host.

    The https and ssh URLs of one repo share a key. Every part is one
    ``safe_path_segment``, so a hostile URL (``https://evil/../../tmp/pwn.git``)
    never escapes the cache root.
    """
    host, segments = repo_url_parts(url)
    if host is None or not segments:
        return (_UNKNOWN, f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.git")
    *parents, leaf = (safe_path_segment(part) for part in segments)
    return (safe_path_segment(host), *parents, f"{leaf}.git")


def cache_path(url: str, *, root: Path) -> Path:
    """The bare-cache path of ``url`` under ``root``."""
    return root.expanduser().resolve().joinpath(*cache_key(url))


def list_caches(root: Path, *, skip: Collection[str] = ()) -> list[Path]:
    """Every ``*.git`` directory under ``root``, sorted; never descends into one.

    Skips symlinks and, at the top level only, hidden directories (names
    starting with ``.``, e.g. scratch dirs) and directories named in ``skip``;
    below it a repo may be hidden (``github.com/acme/.github.git``). A missing
    or unreadable directory is skipped. No git runs.
    """
    found: list[Path] = []
    _collect(root, skip, found, top=True)
    return sorted(found)


def _collect(directory: Path, skip: Collection[str], found: list[Path], *, top: bool) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if top and (entry.name.startswith(".") or entry.name in skip):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            continue
        if entry.name.endswith(".git"):
            found.append(entry)
        else:
            _collect(entry, skip, found, top=False)


def cache_origin(cache: Path) -> str | None:
    """``remote.origin.url`` from ``<cache>/config``, as ``git config --get`` reads it.

    Only the file itself (no includes); the last ``url`` of ``[remote "origin"]``
    wins. ``None`` when the file is unreadable or has no origin URL.
    """
    try:
        lines = (cache / "config").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    origin, in_origin = None, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = _ORIGIN_SECTION.match(stripped) is not None
            continue
        name, sep, value = stripped.partition("=")
        if in_origin and sep and name.strip().lower() == "url":
            origin = _config_value(value) or None
    return origin


def _config_value(raw: str) -> str:
    """A git config value: quotes removed, escapes decoded, a trailing comment dropped."""
    out: list[str] = []
    quoted = False
    chars = iter(raw.strip())
    for char in chars:
        if char == "\\":
            escaped = next(chars, "")
            out.append(_ESCAPES.get(escaped, escaped))
        elif char == '"':
            quoted = not quoted
        elif char in "#;" and not quoted:
            break
        else:
            out.append(char)
    return "".join(out).strip()


def scoped_auth_header(url: str, auth_header: str | None, *, host: str | None) -> str | None:
    """``auth_header`` only when ``url`` is ``https://`` on ``host`` (any case), else ``None``."""
    if auth_header is None or host is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return auth_header if parsed.hostname == host.lower() else None


_UNREAD = object()


class RepoCache:
    """One bare repository cache at ``path``; see the module docstring.

    It does not lock itself: callers hold :meth:`locked` around ``ensure`` and
    ``fetch``. The origin is cached for auth scoping, so repoint it only
    through :meth:`ensure`.
    """

    def __init__(
        self,
        path: Path,
        *,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], Exception] | None = None,
        auth_header: str | None = None,
        auth_host: str | None = None,
        git: str = "git",
        timeout: float = 60.0,
        slow_timeout: float = 600.0,
        lock_timeout: float = 600.0,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._error = error
        self._map_error = map_error
        self._auth_header = auth_header
        self._auth_host = auth_host
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout
        self._lock_timeout = lock_timeout
        self._attempts = attempts
        self._sleep = sleep
        self._origin: object = _UNREAD

    @property
    def path(self) -> Path:
        """The bare repository directory."""
        return self._path

    def exists(self) -> bool:
        """Whether the bare repository has been initialised."""
        return (self._path / "HEAD").is_file()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the sibling ``<cache>.lock`` file for the block."""
        lock = Path(f"{self._path}.lock")
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._error(
                f"could not lock repo cache {self._path}: {exc.strerror or exc}"
            ) from exc
        with file_lock(
            lock,
            timeout=self._lock_timeout,
            error=self._error,
            busy=f"repo cache is busy (another untaped process): {self._path}",
            failed=f"could not lock repo cache {self._path}",
        ):
            yield

    def ensure(self, url: str) -> bool:
        """Create the cache when missing and point ``origin`` at ``url``; True when created."""
        created = not self.exists()
        if created:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self.run(["init", "--bare", "--quiet", str(self._path)], cwd=self._path.parent)
        # ``config`` (not ``remote add``) so no default fetch refspec is written:
        # each capability's ref policy passes its own refspecs.
        if cache_origin(self._path) != url:
            self.run(["config", "--replace-all", "remote.origin.url", url])
        self._origin = _UNREAD
        return created

    def origin(self) -> str | None:
        """The configured ``origin`` URL, or ``None``."""
        return cache_origin(self._path)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
        check: bool = True,
        timeout: float | None = None,
        stdin: bytes | str | None = None,
        retry: bool = False,
        locale_c: bool = True,
    ) -> GitResult:
        """Run ``git <args>`` in the cache (or ``cwd``) with host-scoped auth."""
        origin = self._cached_origin()
        header = scoped_auth_header(origin or "", self._auth_header, host=self._auth_host)
        try:
            return run_git(
                args,
                cwd=self._path if cwd is None else cwd,
                git=self._git,
                timeout=self._timeout if timeout is None else timeout,
                capture=capture,
                check=check,
                stdin=stdin,
                auth_header=header,
                auth_url=origin if header else None,
                locale_c=locale_c,
                ceiling=True,
                retry_transient=retry,
                attempts=self._attempts,
                sleep=self._sleep,
            )
        except GitCommandError as exc:
            if self._map_error is not None:
                raise self._map_error(exc) from exc
            raise self._error(str(exc), **attribution(exc)) from exc

    def fetch(
        self,
        refspecs: Sequence[str] = (),
        *,
        prune: bool = True,
        tags: bool = True,
        depth: int = 0,
        blob_filter: bool = False,
    ) -> None:
        """Fetch ``origin`` with the slow timeout, retrying transient failures."""
        argv = [
            "fetch",
            "--quiet",
            *(["--prune"] if prune else []),
            *(["--no-tags"] if not tags else []),
            *([f"--depth={depth}"] if depth > 0 else []),
            *(["--filter=blob:none"] if blob_filter else []),
            "origin",
            *refspecs,
        ]
        self.run(argv, timeout=self._slow_timeout, retry=True)

    def delete_refs(self, refs: Iterable[str]) -> None:
        """Delete ``refs`` in one ``update-ref --stdin``; a no-op when empty."""
        lines = [f"delete {ref}\n" for ref in refs]
        if lines:
            self.run(["update-ref", "--stdin"], stdin="".join(lines))

    def _cached_origin(self) -> str | None:
        if self._origin is _UNREAD:
            self._origin = self.origin()
        return self._origin if isinstance(self._origin, str) else None
