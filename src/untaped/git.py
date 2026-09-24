"""Hardened ``git`` subprocess execution shared by every capability.

One place owns how untaped shells out to Git: binary resolution, the
non-interactive environment (no terminal or credential-manager prompts,
stdin closed, ssh ``BatchMode`` unless the user configured ssh, C locale so
stderr parsing is locale-independent, inherited repository-redirecting
variables dropped), transient scoped HTTP auth via a private include file,
timeout and exit-status mapping to :class:`GitCommandError` with the auth
header redacted, a bounded retry for transient transport failures, and
deterministic cache paths confined under a managed root.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from untaped.errors import UntapedError

# Lowercased stderr fragments of transport failures that a later identical
# fetch can plausibly survive (dropped TLS/TCP streams, proxy/5xx hiccups).
TRANSIENT_FETCH_MARKERS = (
    "rpc failed",
    "early eof",
    "unexpected disconnect",
    "remote end hung up unexpectedly",
    "connection reset",
    "connection timed out",
    "operation timed out",
    "failed to connect",
    "could not resolve host",
    "gnutls recv error",
    "tls connection was non-properly terminated",
    "ssl_read",
    "invalid index-pack output",
    "index-pack failed",
    "unpack-objects failed",
    "returned error: 429",
    "returned error: 500",
    "returned error: 502",
    "returned error: 503",
    "returned error: 504",
)

# Variables that point git at a different repository than ``cwd`` (set, for
# instance, when untaped runs inside a git hook).
_REPO_REDIRECT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
)
_BATCH_SSH_COMMAND = "ssh -o BatchMode=yes"
_GIST_LIMIT = 300
_REDACTED = "<redacted>"
_LOG = logging.getLogger("untaped.git")
# ``scheme://user[:password]@``: the whole userinfo can be a token.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]+@")


class GitCommandError(UntapedError):
    """A git subprocess could not run, timed out, or exited non-zero.

    ``returncode`` is ``None`` when git never produced an exit status
    (binary missing, launch failure, timeout); ``timed_out`` marks the
    timeout case. ``stderr`` is the full (auth-redacted) stderr text.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        timed_out: bool = False,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.timed_out = timed_out
        self.stderr = stderr


@dataclass(frozen=True)
class GitResult:
    """Outcome of one git invocation; ``stdout`` is empty unless captured."""

    returncode: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str:
        """``stdout`` decoded as UTF-8 (undecodable bytes replaced)."""
        return self.stdout.decode("utf-8", errors="replace")


def git_auth_header(token: str) -> str:
    """Return the transient HTTP ``AUTHORIZATION`` header for a GitHub token."""
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {credential}"


def is_transient_failure(stderr: str) -> bool:
    """Whether ``stderr`` describes a transport failure worth retrying."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in TRANSIENT_FETCH_MARKERS)


def safe_path_segment(value: str) -> str:
    """Map ``value`` to one filesystem-safe path segment (never ``.``/``..``)."""
    safe = _sanitize(value)
    return "_" if safe in {"", ".", ".."} else safe


def safe_cache_path(url: str, *, root: Path) -> Path:
    """Return the deterministic bare-cache path for ``url`` under ``root``.

    The layout is ``<root>/<host>/<name>-<sha256(url)[:16]>.git``; each
    component is a single sanitized segment, so the path never leaves
    ``root``. Changing this function relocates existing caches.
    """
    parsed = urlparse(url)
    if parsed.scheme and parsed.path:
        base_name = Path(parsed.path.rstrip("/")).name
        host = parsed.netloc or "local"
    elif ":" in url and "@" in url.split(":", maxsplit=1)[0]:
        host_part, _, path_part = url.partition(":")
        host = host_part.rsplit("@", maxsplit=1)[-1]
        base_name = Path(path_part.rstrip("/")).name
    else:
        host = "local"
        base_name = Path(url.rstrip("/")).name
    name = base_name.removesuffix(".git") if base_name else "repository"
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    # The digest suffix already keeps the leaf a single safe segment.
    return root.expanduser() / safe_path_segment(host) / f"{_sanitize(name)}-{digest}.git"


@contextmanager
def scoped_auth_config(auth_header: str, *, auth_url: str | None = None) -> Iterator[Path]:
    """Write a private git include file carrying ``auth_header``; delete it on exit.

    With ``auth_url`` the header is scoped to that URL's HTTPS origin;
    without it, it applies to every HTTP remote. The header never appears in
    argv or the environment, only in this 0600 file.
    """
    section = "[http]"
    if auth_url is not None:
        parsed = urlparse(auth_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("git auth header requires an https:// remote URL")
        section = f'[http "https://{parsed.netloc}/"]'
    handle, name = tempfile.mkstemp(prefix="untaped-git-auth-", suffix=".config")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as config:
            config.write(f"{section}\n\textraheader = {auth_header}\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def git_env(
    *,
    git_path: str | None = None,
    cwd: Path | None = None,
    auth_config: Path | None = None,
    locale_c: bool = True,
    batch_ssh: bool = True,
    ceiling: bool = False,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the hardened environment for one git subprocess.

    Always disables terminal and credential-manager prompts and drops
    inherited repository-redirecting variables. ``locale_c`` forces
    untranslated messages; ``batch_ssh`` stops ssh prompting unless the user
    set ``GIT_SSH_COMMAND``/``GIT_SSH``/``core.sshCommand``; ``ceiling`` stops
    repository discovery above ``cwd``; ``auth_config`` includes a
    :func:`scoped_auth_config` file and scrubs trace variables that could log it.
    """
    env = dict(os.environ if base is None else base)
    for name in _REPO_REDIRECT_ENV:
        env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if locale_c:
        env["LC_ALL"] = "C"
        env["LANGUAGE"] = "C"
    if (
        batch_ssh
        and "GIT_SSH_COMMAND" not in env
        and "GIT_SSH" not in env
        and not _core_ssh_command_set(git_path, _config_scope(env))
    ):
        env["GIT_SSH_COMMAND"] = _BATCH_SSH_COMMAND
    if ceiling and cwd is not None:
        parent = str(Path(os.path.abspath(cwd)).parent)
        existing = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(
            [parent, existing] if existing else [parent]
        )
    if auth_config is not None:
        # Git/curl trace output can include the injected Authorization header.
        for key in list(env):
            if key.startswith("GIT_TRACE") or key == "GIT_CURL_VERBOSE":
                del env[key]
        count = _config_count(env)
        env[f"GIT_CONFIG_KEY_{count}"] = "include.path"
        env[f"GIT_CONFIG_VALUE_{count}"] = str(auth_config)
        env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def run_git(
    args: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    git: str = "git",
    capture: bool = False,
    stdin: bytes | str | None = None,
    check: bool = True,
    auth_header: str | None = None,
    auth_url: str | None = None,
    locale_c: bool = True,
    batch_ssh: bool = True,
    ceiling: bool = False,
    retry_transient: bool = False,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> GitResult:
    """Run ``git <args>`` non-interactively and return its result.

    stdout is captured only with ``capture`` (otherwise discarded so git
    chatter never reaches the CLI's own output); stderr is always captured.
    Raises :class:`GitCommandError` when git is missing, cannot start, times
    out, or (with ``check``) exits non-zero. ``retry_transient`` retries
    transport failures up to ``attempts`` times with 1s, 2s, ... backoff; use
    it only for idempotent network commands (fetch, ls-remote).
    """
    argv = list(args)
    label = f"git {argv[0]}" if argv else "git"
    git_path = shutil.which(git)
    if git_path is None:
        raise GitCommandError(f"`{git}` not found on PATH")
    payload = stdin.encode() if isinstance(stdin, str) else stdin
    with _maybe_auth_config(auth_header, auth_url) as auth_config:
        env = git_env(
            git_path=git_path,
            cwd=cwd,
            auth_config=auth_config,
            locale_c=locale_c,
            batch_ssh=batch_ssh,
            ceiling=ceiling,
        )
        for attempt in range(1, max(attempts, 1) + 1):
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    [git_path, *argv],
                    cwd=cwd,
                    env=env,
                    # With no payload, close stdin so git never reads the terminal.
                    stdin=subprocess.DEVNULL if payload is None else None,
                    input=payload,
                    stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise GitCommandError(
                    f"{label} timed out after {timeout:g}s", timed_out=True
                ) from exc
            except OSError as exc:
                raise GitCommandError(f"{label} could not run: {exc}") from exc
            result = GitResult(
                returncode=completed.returncode,
                stdout=_as_bytes(completed.stdout) if capture else b"",
                stderr=redact(_as_bytes(completed.stderr).decode(errors="replace"), auth_header),
            )
            _log_run(argv, cwd, result.returncode, started, auth_header)
            if result.returncode == 0:
                return result
            if retry_transient and attempt < attempts and is_transient_failure(result.stderr):
                sleep(float(2 ** (attempt - 1)))
                continue
            if not check:
                return result
            suffix = f" after {attempt} attempts" if attempt > 1 else ""
            raise GitCommandError(
                f"{label} failed{suffix}: {stderr_gist(result.stderr)}",
                returncode=result.returncode,
                stderr=result.stderr,
            )
    raise AssertionError("unreachable")  # pragma: no cover


def redact(value: str, auth_header: str | None) -> str:
    """Replace ``auth_header`` (and its bare credential) in ``value``."""
    if not auth_header:
        return value
    value = value.replace(auth_header, _REDACTED)
    credential = auth_header.rsplit(" ", maxsplit=1)[-1]
    if len(credential) >= 8:
        value = value.replace(credential, _REDACTED)
    return value


def _log_run(
    argv: Sequence[str],
    cwd: Path | None,
    returncode: int,
    started: float,
    auth_header: str | None,
) -> None:
    """Debug-log one git run: argv with credentials masked, cwd, exit status, time."""
    if not _LOG.isEnabledFor(logging.DEBUG):
        return
    shown = " ".join(_URL_USERINFO.sub(r"\g<scheme>***@", redact(arg, auth_header)) for arg in argv)
    elapsed_ms = (time.monotonic() - started) * 1000
    where = f" in {cwd}" if cwd is not None else ""
    _LOG.debug(
        "git %s%s -> exit %d (%.0f ms)%s",
        shown,
        where,
        returncode,
        elapsed_ms,
        " [auth header]" if auth_header else "",
    )


def stderr_gist(stderr: str) -> str:
    """Keep the ``fatal:``/``error:`` lines (else the last line), bounded."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "no stderr"
    important = [line for line in lines if line.lower().startswith(("fatal:", "error:"))]
    gist = "; ".join(important or lines[-1:])
    if len(gist) > _GIST_LIMIT:
        gist = gist[: _GIST_LIMIT - 3] + "..."
    return gist


@contextmanager
def _maybe_auth_config(auth_header: str | None, auth_url: str | None) -> Iterator[Path | None]:
    if auth_header is None:
        yield None
        return
    with scoped_auth_config(auth_header, auth_url=auth_url) as path:
        yield path


def _sanitize(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value.encode() if isinstance(value, str) else value


def _config_count(env: Mapping[str, str]) -> int:
    try:
        return max(int(env.get("GIT_CONFIG_COUNT", "0")), 0)
    except ValueError:
        return 0


def _config_scope(env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """The environment that decides where git reads global config from."""
    keys = ("HOME", "XDG_CONFIG_HOME")
    return tuple(sorted((k, v) for k, v in env.items() if k in keys or k.startswith("GIT_CONFIG")))


@functools.lru_cache(maxsize=8)
def _core_ssh_command_set(git_path: str | None, scope: tuple[tuple[str, str], ...]) -> bool:
    """Whether git config sets ``core.sshCommand`` (probed once per config scope).

    ``GIT_SSH_COMMAND`` outranks ``core.sshCommand``, so the BatchMode
    default must not be injected over a user's configured ssh command. The
    probe uses ``Popen`` directly so it stays out of the ``subprocess.run``
    path that callers and tests observe.
    """
    if git_path is None:
        return False
    try:
        proc = subprocess.Popen(
            [git_path, "config", "--get", "core.sshCommand"],
            env={**os.environ, **dict(scope), "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return False
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False
    return proc.returncode == 0 and bool(out.strip())
