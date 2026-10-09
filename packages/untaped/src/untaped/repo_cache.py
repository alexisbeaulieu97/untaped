"""Shared bare-repository cache: path scheme, listing, and one git-backed handle.

Layout: ``<root>/<host>/<every URL path segment>.git`` (nested groups
included), so the https and ssh URLs of one repo share one cache, and
``<root>/_unknown/<hash>.git`` for URLs without a host (local paths,
``file://``). Every part is one ``safe_path_segment``, so a hostile URL never
leaves the root.

:class:`RepoCache` is one such bare repository. Its lock is the sibling file
``<cache>.lock`` (via ``file_lock``), so two untaped processes on one cache
serialize and the lock is never inside the repository. Every git call on it
goes through ``run_git`` with ``--git-dir`` naming the cache (so git never
has to discover a bare repository, which ``safe.bareRepository=explicit``
refuses) and carries the auth header only when the cache's origin is an
``https://`` URL on the trusted ``auth_host``; ssh, ``file`` and other-host
origins never see it.

The cache owns mechanics only. Each capability keeps its own root, its ref
policy (what to fetch, what to prune) and its error mapping.
"""

from __future__ import annotations

import hashlib
import os
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
_SPACE = " \t\n\r"
_UNKNOWN = "_unknown"
#: Age after which an interrupted fetch's leftovers are stale: well above the
#: default fetch timeout, so a git running outside untaped keeps its own.
_STALE_LEFTOVER_SECONDS = 3600.0


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

    The https and ssh URLs of one repo share a key. The port is not part of
    it (``repo_url_parts`` drops it): a server's ssh port (often custom, say
    2222) differs from its https port (443), so keying on it would split one
    repo into two caches. Every part is one ``safe_path_segment``, so a
    hostile URL (``https://evil/../../tmp/pwn.git``) never escapes the cache
    root.
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
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError:
        return
    for entry in entries:
        if top and (entry.name.startswith(".") or entry.name in skip):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        path = directory / entry.name
        if entry.name.endswith(".git"):
            found.append(path)
        else:
            _collect(path, skip, found, top=False)


def cache_origin(cache: Path) -> str | None:
    """``remote.origin.url`` from ``<cache>/config``, as ``git config --get`` reads it.

    Only the file itself (no includes); the last ``url`` of ``[remote "origin"]``
    wins. ``None`` when the file is unreadable or has no origin URL.
    """
    try:
        text = (cache / "config").read_text(encoding="utf-8", errors="replace", newline="")
    except OSError:
        return None
    origin, in_origin = None, False
    for line in text.split("\n"):
        stripped = line.strip(_SPACE)
        if stripped.startswith("["):
            in_origin = _ORIGIN_SECTION.match(stripped) is not None
            continue
        name, sep, value = stripped.partition("=")
        if in_origin and sep and name.strip().lower() == "url":
            origin = _config_value(value) or None
    return origin


def _config_value(raw: str) -> str:
    """A git config value, parsed as git's ``parse_value`` does.

    Quotes are removed and escapes decoded; a comment ends the value. Outside
    quotes, leading and trailing whitespace is dropped and each inner
    whitespace character becomes a space. Whitespace is ASCII ``" \\t\\n\\r"``
    only, as git's ``isspace``.
    """
    value = ""
    quoted = False
    spaces = 0
    chars = iter(raw)
    for char in chars:
        if not quoted and char in _SPACE:
            spaces += 1 if value else 0
            continue
        if not quoted and char in "#;":
            break
        value += " " * spaces
        spaces = 0
        if char == "\\":
            escaped = next(chars, "")
            value += _ESCAPES.get(escaped, escaped)
        elif char == '"':
            quoted = not quoted
        else:
            value += char
    return value


def scoped_auth_header(url: str, auth_header: str | None, *, host: str | None) -> str | None:
    """``auth_header`` only when ``url`` is ``https://`` on ``host`` (any case), else ``None``."""
    if auth_header is None or host is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return auth_header if parsed.hostname == host.lower() else None


class RepoCache:
    """One bare repository cache at ``path``; see the module docstring.

    It does not lock itself: callers hold :meth:`locked` around ``ensure`` and
    ``fetch``.
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
        if created or cache_origin(self._path) != url:
            self.run(["config", "--replace-all", "remote.origin.url", url])
        return created

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
        """Run ``git <args>`` on the cache with host-scoped auth.

        With ``cwd`` git runs there instead and finds its repository itself
        (``init`` in the parent, a command in one of the cache's worktrees).
        """
        origin: str | None = None
        header: str | None = None
        if self._auth_header is not None and self._auth_host is not None:
            # Read per call, so a repointed origin is never sent a stale header.
            origin = cache_origin(self._path)
            header = scoped_auth_header(origin or "", self._auth_header, host=self._auth_host)
        try:
            return run_git(
                args,
                cwd=self._path if cwd is None else cwd,
                git_dir=self._path if cwd is None else None,
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
        filter: str | None = None,
    ) -> None:
        """Fetch ``origin`` with the slow timeout, retrying transient failures.

        :meth:`ensure` writes no fetch refspec, so pass ``refspecs``: with
        none, git fetches only the remote ``HEAD`` into ``FETCH_HEAD``.
        ``filter`` is a partial-clone filter spec such as ``"blob:none"``.
        First removes what an interrupted fetch left behind.
        """
        self._remove_stale_fetch_leftovers()
        argv = [
            "fetch",
            "--quiet",
            *(["--prune"] if prune else []),
            *(["--no-tags"] if not tags else []),
            *([f"--depth={depth}"] if depth > 0 else []),
            *([f"--filter={filter}"] if filter is not None else []),
            "origin",
            *refspecs,
        ]
        self.run(argv, timeout=self._slow_timeout, retry=True)

    def _remove_stale_fetch_leftovers(self) -> None:
        """Delete ``objects/pack/tmp_*`` and ``shallow.lock`` older than an hour.

        Any interrupted fetch (a timeout, Ctrl-C, untaped itself killed, or a
        dropped connection) leaves index-pack's temporary files: git has no
        cleanup for them and only ``git prune`` removes them, as this mirrors.
        A shallow fetch killed outright also leaves ``shallow.lock``, which
        makes every later fetch of the cache fail. The caller's cache lock
        keeps other untaped fetches out; the age keeps a git running outside
        untaped safe, except a shallow fetch running over an hour, since git
        writes ``shallow.lock`` once at its start. Best effort.
        """
        cutoff = time.time() - _STALE_LEFTOVER_SECONDS
        candidates = [self._path / "shallow.lock"]
        try:
            with os.scandir(self._path / "objects" / "pack") as scan:
                candidates.extend(Path(e.path) for e in scan if e.name.startswith("tmp_"))
        except OSError:
            pass
        for path in candidates:
            try:
                if path.is_file(follow_symlinks=False) and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def delete_refs(self, refs: Iterable[str]) -> None:
        """Delete ``refs`` in one ``update-ref --stdin``; a no-op when empty."""
        lines = [f"delete {ref}\n" for ref in refs]
        if lines:
            self.run(["update-ref", "--stdin"], stdin="".join(lines))
