"""Running token commands, and the gpg/``pass`` plumbing every store shares.

A plugin declares a :class:`~untaped.sdk.network.credentials.Credential` and core
gets its secret (:mod:`untaped.sdk.network.credentials`); a stored command, or one
``setup`` checks, runs here, without a shell, at most once per process and
only when the secret is first read. Its stripped stdout is the secret.

Token values never reach logs or error messages: a failing command is
reported by program name and exit status only, never by its arguments or
stdout. Its stderr goes straight to the terminal, uncaptured. One exception:
a locked ``pass show <entry>`` names its entry (a name, never a secret) in
the command that unlocks it. Every command runs in the config file's folder
(:func:`command_folder`).

A store may need unlocking first. From a terminal its own prompt asks; with
neither stdin nor stderr on a terminal (an agent, cron), nobody is there to
answer, yet gpg-agent would still draw its pinentry on the inherited
``GPG_TTY``, under whatever owns that screen. So ``pass`` is then told to
fail instead (``--pinentry-mode=error``, see :func:`pass_gpg_options`),
unless the user chose a pinentry mode themselves, and the error says how to
unlock the store from a terminal.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr

from untaped.runtime.settings import resolve_config_path
from untaped.sdk.errors import ConfigError

_LOG = logging.getLogger("untaped.auth")
_TIMEOUT_SECONDS = 60.0
_MASK = "**********"
_NO_PINENTRY = "--pinentry-mode=error"
_cache: dict[tuple[str, ...], str] = {}


class CommandToken(SecretStr):
    """A token read from a command's stdout on first use, then cached.

    Displays masked like any :class:`SecretStr` and never runs the command
    for ``str``/``repr``, so an unused token costs nothing. ``setting`` names
    where the command came from in errors (``the awx token``).
    """

    def __init__(self, argv: list[str], *, setting: str) -> None:
        super().__init__(_MASK)
        self._argv = tuple(argv)
        self._setting = setting

    def get_secret_value(self) -> str:
        """Run the command once per process and return its stripped stdout."""
        return read_command(self._argv, setting=self._setting)


def read_command(argv: Sequence[str], *, setting: str) -> str:
    """Run a token command once per process and return its stripped stdout.

    ``setting`` names where the command came from in errors (``the stored awx
    token``). A relative ``argv[0]`` with a ``/`` is refused
    (exit 4): it would be read from the config file's folder, which a
    command written for another folder never meant.
    """
    key = tuple(argv)
    cached = _cache.get(key)
    if cached is None:
        check_program(argv, setting=setting)
        cached = _run_token_command(key, setting=setting)
        _cache[key] = cached
    return cached


def check_program(argv: Sequence[str], *, setting: str, category: str = "config") -> None:
    """Refuse a command whose program is a relative path (``./get-token``, ``bin/x``).

    A bare name is found on ``PATH`` (:func:`find_program`'s rule) and an
    absolute path is itself; anything else depends on the folder it runs in.
    """
    program = argv[0] if argv else ""
    if "/" in program and not Path(program).expanduser().is_absolute():
        raise ConfigError(
            f"{setting}: the program {program!r} is a relative path; give a bare name found "
            "on PATH or an absolute path",
            category=category,
        )


def run_command(
    argv: Sequence[str],
    *,
    label: str,
    stdin: str | None = None,
    capture_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` without a shell, its stdout captured, and return it finished.

    It runs in :func:`command_folder`, never the current folder. ``label``
    starts every error, which never repeats the command's output or
    arguments. stderr goes straight to the terminal unless ``capture_stderr``.
    A non-zero exit is returned, not raised. With nobody to answer a prompt,
    ``pass`` (also inside a wrapper) runs with :func:`pass_gpg_env`.
    """
    env = pass_gpg_env(english=Path(argv[0]).name == "pass")
    try:
        return subprocess.run(
            list(argv),
            input=stdin or "",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else None,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=env,
            cwd=command_folder(),
        )
    except FileNotFoundError:
        raise ConfigError(f"{label} not found on PATH") from None
    except subprocess.TimeoutExpired:
        # Stopping pass leaves gpg-agent's pinentry open, holding the agent for every later read.
        stuck = (
            "`gpgconf --kill gpg-agent` closes a passphrase prompt left open (it also "
            "forgets cached passphrases, SSH key unlocks included); "
        )
        raise ConfigError(
            f"{label} timed out after {_TIMEOUT_SECONDS:g}s; it may be waiting on an "
            "unlock prompt on a screen nobody sees (over SSH, unlock the store first)",
            category="unavailable",
            hint=f"{stuck}then retry" if Path(argv[0]).name == "pass" else None,
        ) from None
    except OSError as exc:
        raise ConfigError(f"{label} could not run: {exc.strerror}") from None


def command_folder() -> Path:
    """The folder token and store commands run in: the config file's (``~/.untaped``).

    Never the current folder, so a repository can't plant the program that a
    relative ``argv[0]``, or a bare name with ``.`` on ``PATH``, would run.
    Home, until the config folder exists.
    """
    folder = resolve_config_path().absolute().parent
    return folder if folder.is_dir() else Path.home()


def find_program(name: str) -> str | None:
    """Where a bare program ``name`` is found on ``PATH`` when untaped runs it.

    Like ``shutil.which(name)``, except that a relative ``PATH`` entry (``.``,
    or an empty one) is read from :func:`command_folder`, never the current
    folder, so a repository can't plant the program untaped probes for or
    runs. Pass a bare name: one with a ``/`` is resolved as ``shutil.which``
    would.
    """
    folder = command_folder()
    entries = os.environ.get("PATH", os.defpath).split(os.pathsep)
    path = os.pathsep.join(str(folder / (entry or ".")) for entry in entries)
    return shutil.which(name, path=path)


def pass_gpg_options() -> list[str]:
    """The gpg options ``pass`` runs with: ``PASSWORD_STORE_GPG_OPTS``, split.

    Plus ``--pinentry-mode=error`` while :func:`_suppressing_prompts`, so a
    locked key fails at once instead of drawing a prompt nobody can answer.
    """
    options = os.environ.get("PASSWORD_STORE_GPG_OPTS", "").split()
    return [*options, _NO_PINENTRY] if _suppressing_prompts() else options


def pass_gpg_env(*, english: bool = True) -> dict[str, str] | None:
    """The environment for a ``pass`` or gpg run, or ``None`` to inherit it unchanged.

    While prompts are suppressed: :func:`pass_gpg_options` for ``pass``, and,
    with ``english`` (a run whose stderr untaped reads: ``pass`` itself, or
    gpg), gpg's messages in English so its "No pinentry" reads the same
    everywhere. Any other command keeps the user's locale.
    """
    if not _suppressing_prompts():
        return None
    env = {**os.environ, "PASSWORD_STORE_GPG_OPTS": " ".join(pass_gpg_options())}
    return {**env, "LC_ALL": "C", "LANGUAGE": "C"} if english else env


def _suppressing_prompts() -> bool:
    """Whether untaped tells gpg not to ask for a passphrase in this process.

    Only when nobody can answer, and the user chose no pinentry mode (in
    ``PASSWORD_STORE_GPG_OPTS`` or ``gpg.conf``): a loopback mode with a
    passphrase file, or ``--pinentry-mode=ask`` for a desktop dialog, is
    theirs to keep.
    """
    return not _someone_can_answer() and not _user_pinentry_mode()


def _someone_can_answer() -> bool:
    """Whether stdin or stderr is a terminal.

    A person in a pipeline keeps stderr on their terminal, so someone is there
    to answer pinentry (which draws on ``GPG_TTY``). An agent captures both
    streams, even when its commands keep the user's terminal as their
    controlling terminal (and inherit ``GPG_TTY``), so ``/dev/tty``, which a
    confirmation may reach, is no sign here.
    """
    from untaped.runtime.render import stream_is_tty  # noqa: PLC0415 - keep auth imports light

    return stream_is_tty(sys.stdin) or stream_is_tty(sys.stderr)


def _user_pinentry_mode() -> bool:
    options = os.environ.get("PASSWORD_STORE_GPG_OPTS", "").split()
    if any(opt == "--pinentry-mode" or opt.startswith("--pinentry-mode=") for opt in options):
        return True
    home = os.environ.get("GNUPGHOME") or Path.home() / ".gnupg"
    try:
        text = (Path(home) / "gpg.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.strip().startswith("pinentry-mode") for line in text.splitlines())


def command_failure(
    label: str, completed: subprocess.CompletedProcess[str], *, is_pass: bool
) -> ConfigError:
    """The error for a command that exited non-zero, starting with ``label``.

    For ``pass``, whose stderr was captured: gpg repeats one error per call,
    so its first non-empty line is quoted, with gpg's fix as the hint. A store
    left locked because nobody can answer its prompt says so instead.
    """
    message = f"{label} exited with status {completed.returncode}"
    if not is_pass:
        return ConfigError(message)
    if locked_without_terminal(completed.stderr):
        return locked_store_error(label, unlock=_pass_unlock(completed.args))
    if quote := first_stderr_line(completed.stderr):
        message += f": {quote}"
    return ConfigError(message, hint=gpg_hint(completed.stderr))


def locked_without_terminal(stderr: str) -> bool:
    """Whether gpg failed only because untaped told it not to ask for the passphrase."""
    return "No pinentry" in stderr and _suppressing_prompts()


def locked_store_error(label: str, *, unlock: str | None = None) -> ConfigError:
    """The error for a gpg key left locked because nobody at a terminal could unlock it.

    ``unlock`` is a command that unlocks it from a terminal, when one can be named.
    """
    how = f"`{unlock}`" if unlock else "the same command"
    return ConfigError(
        f"{label}: the password store is locked, and untaped does not ask for its "
        "passphrase without a terminal (as when an agent runs it)",
        category="unavailable",
        hint=f"unlock it from your own terminal by running {how}, then retry",
    )


def forget_token_command(argv: list[str]) -> None:
    """Forget the cached result of ``argv``, so its next read runs the command again.

    For a caller that checks a token it was just given (``setup``): a retry after a rejected
    token must run the command again, not read the rejected result back.
    """
    _cache.pop(tuple(argv), None)


def clear_token_cache() -> None:
    """Forget every token command's result (tests, embedding)."""
    _cache.clear()


PASS_GPG_HINT = (
    "check that gpg works on its own (`echo test | gpg -e -r <gpg-id> | gpg -d`); usual causes: "
    "no pinentry program installed, GPG_TTY unset (`export GPG_TTY=$(tty)`), or gpg-agent not "
    "running (`gpgconf --launch gpg-agent`)"
)
"""The usual fixes when ``pass`` cannot decrypt; named once per failing command."""

TRUST_HINT = (
    "gpg does not trust the key yet: run `gpg --edit-key <gpg-id> trust` and pick 5 "
    "(ultimate), or `gpg --import-ownertrust`"
)
"""The fix when gpg refuses an imported key whose ownertrust is unknown."""

_STDERR_QUOTE = 120


def gpg_hint(stderr: str) -> str | None:
    """The fix to name for a failure whose stderr is gpg's, else ``None``.

    A ``pass`` that fails for its own reasons (no such entry, a protected
    one) gets no gpg advice.
    """
    if not any(line.startswith("gpg:") for line in stderr.splitlines()):
        return None
    if "no assurance" in stderr or "Unusable public key" in stderr:
        return TRUST_HINT
    return PASS_GPG_HINT


def first_stderr_line(stderr: str) -> str:
    """The first non-empty stderr line, cut short: what a repeating tool failed on."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    # gpg may open with a note (a trust warning); the failure is the line saying so.
    line = next((line for line in lines if "failed" in line or "error" in line.lower()), "")
    return (line or next(iter(lines), ""))[:_STDERR_QUOTE]


def _run_token_command(argv: tuple[str, ...], *, setting: str) -> str:
    label = f"{setting}: {argv[0]!r}"
    _LOG.debug("%s: running %s", setting, argv[0])
    # stderr goes straight to the terminal: the command explains its own
    # failures, and untaped never captures or repeats them. Except `pass`,
    # whose gpg repeats one error per call: the first line and one hint do.
    is_pass = Path(argv[0]).name == "pass"
    completed = run_command(argv, label=label, capture_stderr=is_pass)
    if completed.returncode != 0:
        raise command_failure(label, completed, is_pass=is_pass)
    token = completed.stdout.strip()
    if not token:
        raise ConfigError(f"{label} printed no token")
    return token


def _pass_unlock(argv: Sequence[str]) -> str | None:
    """``pass show <entry> > /dev/null`` for a plain ``pass show <entry>``, else ``None``.

    Only that shape is repeated: its one argument is an entry name, never a
    secret, while any other command's arguments are never shown.
    """
    if len(argv) != 3 or Path(argv[0]).name != "pass" or argv[1] != "show":
        return None
    return f"pass show {shlex.quote(argv[2])} > /dev/null"


__all__ = [
    "PASS_GPG_HINT",
    "TRUST_HINT",
    "CommandToken",
    "check_program",
    "clear_token_cache",
    "command_failure",
    "command_folder",
    "find_program",
    "first_stderr_line",
    "forget_token_command",
    "gpg_hint",
    "locked_store_error",
    "locked_without_terminal",
    "pass_gpg_env",
    "pass_gpg_options",
    "read_command",
    "run_command",
]
