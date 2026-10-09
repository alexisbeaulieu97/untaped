"""Hardened ``git`` subprocess execution shared by every plugin.

One place owns how untaped shells out to Git: binary resolution, the
non-interactive environment (no terminal or credential-manager prompts,
stdin closed, ssh ``BatchMode`` unless the user configured ssh, C locale so
stderr parsing is locale-independent, inherited repository-redirecting
variables dropped), transient scoped HTTP auth via a private include file,
timeout and exit-status mapping to :class:`GitCommandError` with the auth
header redacted, a bounded retry for transient transport failures, and the
work-tree root lookup. A bare repository is always named with ``--git-dir``
(``git_dir=``), never found from ``cwd``: hardened shells such as GitHub
Copilot CLI set ``safe.bareRepository=explicit``, which refuses that discovery.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from untaped.errors import ErrorCategory, UntapedError

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
# Seconds a timed-out git gets, after SIGTERM, to remove its lock files.
_TERM_GRACE_S = 2.0
_REDACTED = "<redacted>"
_LOG = logging.getLogger("untaped.git")
# ``scheme://user[:password]@``: the whole userinfo can be a token.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]+@")


class GitCommandError(UntapedError):
    """A git subprocess could not run, timed out, or exited non-zero.

    ``returncode`` is ``None`` when git never produced an exit status
    (binary missing, launch failure, timeout); ``timed_out`` marks the
    timeout case. ``stderr`` is the full (auth-redacted) stderr text.
    Its category is ``failed`` in ``git``; a timeout or a transient transport
    failure is ``unavailable``, and a remote refusing the credentials is
    ``auth`` (``permission`` for HTTP 403), with a hint.
    """

    system = "git"

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        timed_out: bool = False,
        stderr: str = "",
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
    ) -> None:
        if category is None and timed_out:
            category = ErrorCategory.UNAVAILABLE
        super().__init__(message, category=category, system=system, hint=hint)
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


# Lowercased stderr fragments of a remote refusing the credentials (auth) or
# the account (permission): retrying cannot help, the credentials must change.
_AUTH_MARKERS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "permission denied (publickey",
    "invalid username or password",
    "returned error: 401",
)
_PERMISSION_MARKERS = ("returned error: 403",)
_CREDENTIAL_HINT = (
    "the remote rejected the credentials: check the token (or ssh key) "
    "for this host and its access to the repository"
)


def credential_failure(stderr: str) -> ErrorCategory | None:
    """``auth``/``permission`` when ``stderr`` says the remote refused the credentials."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        return ErrorCategory.PERMISSION
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return ErrorCategory.AUTH
    return None


def _failure_category(stderr: str) -> ErrorCategory | None:
    """The category of a git failure from its stderr (``None``: plain ``failed``)."""
    credential = credential_failure(stderr)
    if credential is not None:
        return credential
    return ErrorCategory.UNAVAILABLE if is_transient_failure(stderr) else None


def safe_path_segment(value: str) -> str:
    """Map ``value`` to one filesystem-safe path segment (never ``.``/``..``)."""
    safe = _sanitize(value)
    return "_" if safe in {"", ".", ".."} else safe


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
    git_dir: Path | None = None,
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
    repository discovery above ``cwd``; ``git_dir`` is the repository whose
    ``core.sshCommand`` counts (else the one at ``cwd``); ``auth_config``
    includes a :func:`scoped_auth_config` file and scrubs trace variables that could log it.
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
        and not _core_ssh_command_set(
            git_path, _config_scope(env), _probe_dir(cwd), _probe_git_dir(git_dir)
        )
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
    git_dir: Path | None = None,
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
    out, or (with ``check``) exits non-zero. ``git_dir`` names the repository
    with ``--git-dir`` (required for a bare one; see the module docstring).
    ``retry_transient`` retries transport failures up to ``attempts`` times
    with 1s, 2s, ... backoff; use it only for idempotent network commands
    (fetch, ls-remote).
    """
    argv = list(args)
    label = f"git {argv[0]}" if argv else "git"
    git_path = shutil.which(git)
    if git_path is None:
        raise GitCommandError(f"`{git}` not found on PATH", category="config", system="local")
    payload = stdin.encode() if isinstance(stdin, str) else stdin
    with _maybe_auth_config(auth_header, auth_url) as auth_config:
        env = git_env(
            git_path=git_path,
            cwd=cwd,
            git_dir=git_dir,
            auth_config=auth_config,
            locale_c=locale_c,
            batch_ssh=batch_ssh,
            ceiling=ceiling,
        )
        for attempt in range(1, max(attempts, 1) + 1):
            started = time.monotonic()
            try:
                completed = _run_process(
                    [git_path, *_git_dir_option(git_dir), *argv],
                    cwd=cwd,
                    env=env,
                    # With no payload, close stdin so git never reads the terminal.
                    stdin=subprocess.DEVNULL if payload is None else None,
                    input=payload,
                    stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
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
                category=_failure_category(result.stderr),
                hint=_CREDENTIAL_HINT if credential_failure(result.stderr) else None,
            )
    raise AssertionError("unreachable")  # pragma: no cover


def _run_process(
    cmd: list[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
    stdin: int | None,
    input: bytes | None,
    stdout: int,
    stderr: int,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """``subprocess.run`` for git, except that a timeout stops git's whole process group.

    Git runs in a new session, so the helpers it starts (``remote-http``,
    ``fetch-pack``, ``index-pack``) share its process group and nothing it
    starts can prompt on the terminal. On a timeout or an interrupt the group
    gets SIGTERM, so git removes its lock files, then SIGKILL after
    :data:`_TERM_GRACE_S`, so no helper outlives the call.
    """
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input is not None else stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    ) as process:
        try:
            out, err = process.communicate(input, timeout=timeout)
        except BaseException:
            _stop_group(process)
            raise
    return subprocess.CompletedProcess(cmd, process.returncode, out, err)


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    """SIGTERM the process group led by ``process``, wait for it to empty, then SIGKILL it."""
    deadline = time.monotonic() + _TERM_GRACE_S
    _signal_group(process.pid, signal.SIGTERM)
    try:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_TERM_GRACE_S)
        # Helpers can outlive git: give them the rest of the grace period too.
        while time.monotonic() < deadline and _signal_group(process.pid, 0):
            time.sleep(0.02)
    finally:  # a second interrupt during the grace period must not skip the kill
        _signal_group(process.pid, signal.SIGKILL)


def _signal_group(pgid: int, sig: int) -> bool:
    """Send ``sig`` to process group ``pgid``; ``False`` when no process is left in it."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError, PermissionError:  # macOS: EPERM for a group of zombies
        return False
    return True


def git_toplevel(path: Path, *, git: str = "git", timeout: float = 30.0) -> Path | None:
    """Return the resolved root of the git work tree containing directory ``path``.

    ``path`` must be an existing directory: git runs there, so a missing path
    cannot launch it. ``None`` means git ran and found no work tree there
    (outside any checkout, or inside a bare repository). Failures to run git
    at all (binary missing, launch failure, timeout) raise
    :class:`GitCommandError`, so callers never mistake a broken git for "not a
    checkout".
    """
    result = run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=path,
        git=git,
        timeout=timeout,
        capture=True,
        check=False,
        batch_ssh=False,  # purely local: skip the core.sshCommand probe
    )
    top = result.text.strip() if result.returncode == 0 else ""
    return Path(top).resolve() if top else None


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


def _git_dir_option(git_dir: Path | None) -> list[str]:
    return [] if git_dir is None else [f"--git-dir={os.path.abspath(git_dir)}"]


def _probe_git_dir(git_dir: Path | None) -> str | None:
    """The repository the ``core.sshCommand`` probe names with ``--git-dir``, when it exists."""
    return os.path.abspath(git_dir) if git_dir is not None and os.path.isdir(git_dir) else None


def _probe_dir(cwd: Path | None) -> str | None:
    """The directory whose repository config the ``core.sshCommand`` probe reads."""
    return str(cwd) if cwd is not None and os.path.isdir(cwd) else None


@functools.lru_cache(maxsize=256)
def _core_ssh_command_set(
    git_path: str | None,
    scope: tuple[tuple[str, str], ...],
    cwd: str | None,
    git_dir: str | None,
) -> bool:
    """Whether git config sets ``core.sshCommand`` (probed once per scope and repo).

    ``GIT_SSH_COMMAND`` outranks ``core.sshCommand``, so the BatchMode
    default must not be injected over a user's configured ssh command. The
    probe runs in the command's ``cwd`` (the process cwd when there is none)
    and names ``git_dir`` when given, so it reads that repository's config,
    never one inherited ``GIT_DIR`` points at. It uses ``Popen`` directly so
    it stays out of the ``subprocess.run`` path that callers and tests observe.
    """
    if git_path is None:
        return False
    inherited = {k: v for k, v in os.environ.items() if k not in _REPO_REDIRECT_ENV}
    try:
        proc = subprocess.Popen(
            [
                git_path,
                *([f"--git-dir={git_dir}"] if git_dir is not None else []),
                "config",
                "--get",
                "core.sshCommand",
            ],
            cwd=cwd,
            env={**inherited, **dict(scope), "GIT_TERMINAL_PROMPT": "0"},
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
