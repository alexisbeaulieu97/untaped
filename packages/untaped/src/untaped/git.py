"""Hardened ``git`` subprocess execution shared by every plugin.

One place owns how untaped shells out to Git: binary resolution, the
non-interactive environment (no terminal or credential-manager prompts,
stdin closed, ssh ``BatchMode`` unless the user configured ssh, C locale so
stderr parsing is locale-independent, inherited repository-redirecting
variables dropped), secret settings (credentials) through a private include
file, timeout and exit-status mapping to :class:`GitCommandError` with those
secrets redacted, a bounded retry for transient transport failures, and the
work-tree root lookup. A bare repository is always named with ``--git-dir``
(``git_dir=``), never found from ``cwd``: hardened shells such as GitHub
Copilot CLI set ``safe.bareRepository=explicit``, which refuses that discovery.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

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
# Process groups of the git commands running now, for forward_signals().
_LIVE_GROUPS: dict[int, None] = {}
# Signals forward_signals() passed on, for a group registered just after one.
_FORWARDED: list[int] = []
# Threads forking a git not yet in _LIVE_GROUPS, for a signal that ends untaped.
# Dict set and pop are atomic, so neither a lock nor an interrupt can wedge it.
# An interrupt can leave the main thread's own mark behind; that is harmless
# only because the handler never waits on its own thread. A signal handled on
# the main thread while it is itself mid-spawn still misses that git (#579).
_SPAWNING: dict[int, None] = {}
# How long a signal that ends untaped waits for those to register.
_SPAWN_WAIT_S = 1.0
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
    "unable to get password",
    "terminal prompts disabled",
    "permission denied (publickey",
    "invalid username or password",
    "returned error: 401",
)
_PERMISSION_MARKERS = ("returned error: 403",)
_CREDENTIAL_HINT = (
    "the remote rejected the credentials: check the token "
    "for this host and its access to the repository"
)
# Lowercased stderr fragments of ssh failing where it would have prompted;
# a host key that changed is never one to accept from a hint.
_SSH_PROMPT_MARKERS = ("host key verification failed", "ssh_askpass:")
_UNTRUSTED_HOST_KEY = ("remote host identification has changed", "revoked host key")
# ssh reports a passphrase it could not ask for only as a refused key.
_SSH_KEY_REFUSED = "permission denied (publickey"
_SSH_KEY_HINT = (
    "the remote refused the ssh key: check the key's access to the repository; "
    "untaped runs git without a terminal, so a key with a passphrase must be "
    "loaded into ssh-agent (`ssh-add`)"
)
_SSH_HINT = (
    "untaped runs git without a terminal, so ssh cannot ask for a key "
    "passphrase or to trust a new host: load the key into ssh-agent "
    "(`ssh-add`), or accept the host key once with your own `git fetch`"
)


def credential_failure(stderr: str) -> ErrorCategory | None:
    """``auth``/``permission`` when ``stderr`` says the remote refused the credentials."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        return ErrorCategory.PERMISSION
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return ErrorCategory.AUTH
    return None


def _failure_hint(stderr: str) -> str | None:
    """What the user can do about a git failure, from its stderr."""
    lowered = stderr.lower()
    if any(m in lowered for m in _UNTRUSTED_HOST_KEY):
        return None  # never a key to accept on a hint's say-so
    if any(m in lowered for m in _SSH_PROMPT_MARKERS):
        return _SSH_HINT
    if _SSH_KEY_REFUSED in lowered:
        return _SSH_KEY_HINT
    return _CREDENTIAL_HINT if credential_failure(stderr) else None


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
def _auth_include(settings: Mapping[str, str]) -> Iterator[Path]:
    """Write ``settings`` (``section[.subsection].name`` → value) to a private include file.

    The values never appear in argv or the environment, only in this 0600
    file, which is deleted on exit.
    """
    handle, name = tempfile.mkstemp(prefix="untaped-git-auth-", suffix=".config")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as config:
            config.writelines(_config_entry(key, value) for key, value in settings.items())
        yield path
    finally:
        path.unlink(missing_ok=True)


def _config_entry(key: str, value: str) -> str:
    """One ``key = value`` as a git config file section, value quoted."""
    section, _, rest = key.partition(".")
    subsection, _, name = rest.rpartition(".")
    if not section or not name or "\n" in key or "\n" in value:
        raise ValueError(f"not a git config key and one-line value: {key!r}")
    header = f"[{section}]" if not subsection else f'[{section} "{_quoted(subsection)}"]'
    return f'{header}\n\t{name} = "{_quoted(value)}"\n'


def _quoted(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


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
    config: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the hardened environment for one git subprocess.

    Always disables terminal and credential-manager prompts and drops
    inherited repository-redirecting variables. ``locale_c`` forces
    untranslated messages; ``batch_ssh`` stops ssh prompting unless the user
    set ``GIT_SSH_COMMAND``/``GIT_SSH``/``core.sshCommand``; ``ceiling`` stops
    repository discovery above ``cwd``; ``git_dir`` is the repository whose
    ``core.sshCommand`` counts (else the one at ``cwd``); ``auth_config``
    includes a private file of secret settings and scrubs trace variables that could log them.
    ``config`` adds command-scope settings (``git -c``'s scope, outside argv).
    """
    env = dict(os.environ if base is None else base)
    for name in _REPO_REDIRECT_ENV:
        env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    # Without a terminal, ssh and git would fall back to a graphical askpass
    # prompt (git tries SSH_ASKPASS too); a user's own GIT_ASKPASS, such as a
    # CI token script, still runs.
    if not env.get("SSH_ASKPASS_REQUIRE"):
        env["SSH_ASKPASS_REQUIRE"] = "never"
    env.setdefault("GIT_ASKPASS", "")
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
    for key, value in (config or {}).items():
        count = _config_count(env)
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        env["GIT_CONFIG_COUNT"] = str(count + 1)
    if auth_config is not None:
        # Git/curl trace output can include the injected Authorization header.
        for key in list(env):
            if _is_trace_variable(key):
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
    auth_config: Mapping[str, str] | None = None,
    locale_c: bool = True,
    batch_ssh: bool = True,
    ceiling: bool = False,
    retry_transient: bool = False,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    config: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> GitResult:
    """Run ``git <args>`` non-interactively and return its result.

    stdout is captured only with ``capture`` (otherwise discarded so git
    chatter never reaches the CLI's own output); stderr is always captured.
    Raises :class:`GitCommandError` when git is missing, cannot start, times
    out, or (with ``check``) exits non-zero. ``git_dir`` names the repository
    with ``--git-dir`` (required for a bare one; see the module docstring).
    ``retry_transient`` retries transport failures up to ``attempts`` times
    with 1s, 2s, ... backoff; use it only for idempotent network commands
    (fetch, ls-remote). ``config`` sets command-scope git settings, as
    ``git -c key=value`` would but outside argv, so error messages still name
    the subcommand; ``auth_config`` does the same for settings that carry a
    secret (a credential header): they go in a private include file, never
    argv or the environment, trace variables that could log them are dropped,
    and their values are redacted from stderr and the debug log. ``env`` adds
    variables on top of the hardened environment.
    """
    argv = list(args)
    label = f"git {argv[0]}" if argv else "git"
    git_path = shutil.which(git)
    if git_path is None:
        raise GitCommandError(f"`{git}` not found on PATH", category="config", system="local")
    payload = stdin.encode() if isinstance(stdin, str) else stdin
    secrets = tuple(value for value in (auth_config or {}).values() if value)
    with _maybe_auth_include(auth_config) as include:
        process_env = git_env(
            git_path=git_path,
            cwd=cwd,
            git_dir=git_dir,
            auth_config=include,
            locale_c=locale_c,
            batch_ssh=batch_ssh,
            ceiling=ceiling,
            config=config,
        )
        process_env.update(
            (key, value)
            for key, value in (env or {}).items()
            # Trace output can carry the injected header (see git_env).
            if auth_config is None or not _is_trace_variable(key)
        )
        for attempt in range(1, max(attempts, 1) + 1):
            started = time.monotonic()
            try:
                completed = _run_process(
                    [git_path, *_git_dir_option(git_dir), *argv],
                    cwd=cwd,
                    env=process_env,
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
                stderr=redact(_as_bytes(completed.stderr).decode(errors="replace"), secrets),
            )
            _log_run(argv, cwd, result.returncode, started, secrets)
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
                hint=_failure_hint(result.stderr),
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
    :data:`_TERM_GRACE_S`, so no helper outlives the call. Signals sent to
    untaped's own process group reach git only through :func:`forward_signals`.
    """
    forwarded = len(_FORWARDED)
    spawner = threading.get_ident()
    _SPAWNING[spawner] = None
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input is not None else stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    except BaseException:
        _SPAWNING.pop(spawner, None)
        raise
    with process:
        try:
            # Register before clearing the mark: a dying untaped signals the
            # groups again once no other thread is mid-spawn.
            _LIVE_GROUPS[process.pid] = None
            _SPAWNING.pop(spawner, None)
            # A signal forwarded between Popen and registration missed this group.
            for sig in _FORWARDED[forwarded:]:
                _signal_group(process.pid, sig)
            out, err = process.communicate(input, timeout=timeout)
        except BaseException:
            _stop_group(process)
            raise
        finally:
            _SPAWNING.pop(spawner, None)
            _LIVE_GROUPS.pop(process.pid, None)
    return subprocess.CompletedProcess(cmd, process.returncode, out, err)


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    """SIGTERM the process group led by ``process``, wait for it to empty, then SIGKILL it."""
    if not hasattr(os, "killpg"):  # no process groups (Windows)
        process.kill()
        return
    deadline = time.monotonic() + _TERM_GRACE_S
    alive = _signal_group(process.pid, signal.SIGTERM)
    try:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_TERM_GRACE_S)
        # Helpers can outlive git: give them the rest of the grace period too.
        while alive and time.monotonic() < deadline:
            alive = _signal_group(process.pid, 0)
            if alive:
                time.sleep(0.02)
    finally:  # a second interrupt during the grace period must not skip the kill
        if alive:
            _signal_group(process.pid, signal.SIGKILL)


def _signal_group(pgid: int, sig: int) -> bool:
    """Send ``sig`` to process group ``pgid``; ``False`` when no process is left in it."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError, PermissionError:  # macOS: EPERM for a group of zombies
        return False
    return True


def forward_signals() -> None:
    """Pass SIGINT, SIGTERM and SIGHUP on to the git commands running when they arrive.

    Git runs in its own session (see :func:`_run_process`), so a signal sent
    to untaped's process group (Ctrl-C, a closed terminal, ``timeout``, a
    cancelled CI job) no longer reaches it, and git started by a worker
    thread would run on until its own timeout. The CLI entry point calls this
    once, from the main thread; the previous handler then runs as before, and
    a signal untaped ignores stays ignored.
    """
    if not hasattr(os, "killpg"):
        return  # no process groups (Windows)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous = signal.getsignal(sig)
        if previous is None or previous == signal.SIG_IGN:
            continue
        signal.signal(sig, functools.partial(_forward_signal, previous))


def _forward_signal(previous: Callable[..., object] | int, signum: int, frame: object) -> None:
    _FORWARDED.append(signum)
    for pgid in list(_LIVE_GROUPS):
        _signal_group(pgid, signum)
    if callable(previous):
        previous(signum, frame)
    else:  # SIG_DFL: die of the signal, as untaped did before
        # A git another thread is starting would outlive untaped: let it register.
        deadline = time.monotonic() + _SPAWN_WAIT_S
        me = threading.get_ident()  # a spawn on this thread cannot finish meanwhile
        while any(t != me for t in list(_SPAWNING)) and time.monotonic() < deadline:
            time.sleep(0.01)
        for pgid in list(_LIVE_GROUPS):
            _signal_group(pgid, signum)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        os._exit(128 + signum)  # still here: PID 1 ignores its own default signals


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


def redact(value: str, secrets: Sequence[str]) -> str:
    """Replace each of ``secrets`` (and its last word, a header's bare credential) in ``value``."""
    for secret in secrets:
        value = value.replace(secret, _REDACTED)
        credential = secret.rsplit(" ", maxsplit=1)[-1]
        if len(credential) >= 8:
            value = value.replace(credential, _REDACTED)
    return value


def _log_run(
    argv: Sequence[str],
    cwd: Path | None,
    returncode: int,
    started: float,
    secrets: Sequence[str],
) -> None:
    """Debug-log one git run: argv with credentials masked, cwd, exit status, time."""
    if not _LOG.isEnabledFor(logging.DEBUG):
        return
    shown = " ".join(_URL_USERINFO.sub(r"\g<scheme>***@", redact(arg, secrets)) for arg in argv)
    elapsed_ms = (time.monotonic() - started) * 1000
    where = f" in {cwd}" if cwd is not None else ""
    _LOG.debug(
        "git %s%s -> exit %d (%.0f ms)%s",
        shown,
        where,
        returncode,
        elapsed_ms,
        " [auth config]" if secrets else "",
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
def _maybe_auth_include(settings: Mapping[str, str] | None) -> Iterator[Path | None]:
    if not settings:
        yield None
        return
    with _auth_include(settings) as path:
        yield path


def _sanitize(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value.encode() if isinstance(value, str) else value


def _is_trace_variable(name: str) -> bool:
    return name.startswith("GIT_TRACE") or name == "GIT_CURL_VERBOSE"


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
    it stays out of the :func:`_run_process` path that callers and tests observe.
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
