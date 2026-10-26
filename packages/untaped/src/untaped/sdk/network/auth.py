"""Running token commands, and the 11.0 way a settings section supplies its token.

A plugin that declares a :class:`~untaped.sdk.network.credentials.Credential` gets its
secret from core (:mod:`untaped.sdk.network.credentials`). This module keeps what both
ways share, running a token command and the gpg/``pass`` plumbing, and the
11.0 way, for a plugin that has not moved: a ``token`` and a
``token_command`` setting beside a ``token_sources: ClassVar[TokenSources]``
attribute, read by name. Precedence there, first match wins:

1. ``<section>.token`` (config file or its ``UNTAPED_<SECTION>__TOKEN``
   override);
2. ``<section>.token_command``: an argv list run without a shell, at most
   once per process and only when the token is first read; its stripped
   stdout is the token;
3. the section's conventional environment variables, in declared order
   (for example ``GH_TOKEN`` then ``GITHUB_TOKEN``).

A folder's file that set any of such a section's settings makes the section
one key a folder chose: its token from a variable or a setting is refused,
as for a declared credential.

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

Support for a token stored in plain text in the config file is the
deprecated behaviour :data:`PLAINTEXT_TOKEN`, which names its removal
version: using one warns once per section per process.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

from untaped.runtime.settings import env_var_name, env_var_setting, resolve_config_path
from untaped.sdk._run import current_overlay
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.marks import TokenCommand, TokenSources  # type: ignore[deprecated]
from untaped.sdk.marks import deprecated_behaviour

_LOG = logging.getLogger("untaped.auth")
_TIMEOUT_SECONDS = 60.0
_MASK = "**********"
_NO_PINENTRY = "--pinentry-mode=error"
_cache: dict[tuple[str, ...], str] = {}

PLAINTEXT_TOKEN = deprecated_behaviour(
    "plaintext-token", removed_in=12, replacement="untaped auth migrate"
)
"""Support for ``<section>.token`` stored in plain text in ``config.yml`` (removal: #670)."""


def plaintext_subject(section: str, where: str, *, field: str = "token") -> str:
    """How every site names what goes: ``support for a plain-text awx.token in config.yml``."""
    return f"support for a plain-text {section}.{field} in {where}"


def _legacy_env(model: type[BaseModel]) -> tuple[str, ...] | None:
    """The variables of an 11.0 section's ``token`` (``token_sources``), or ``None`` for none.

    ``None`` for a model whose plugin declares credentials, or with no
    ``token`` setting.
    """
    if "token" not in model.model_fields:
        return None
    sources = getattr(model, "token_sources", None)
    return tuple(sources.env) if isinstance(sources, TokenSources) else ()


class CommandToken(SecretStr):
    """A token read from ``token_command`` stdout on first use, then cached.

    Displays masked like any :class:`SecretStr` and never runs the command
    for ``str``/``repr``, so an unused token costs nothing. ``command`` names
    the setting it came from in errors (``token_command``).
    """

    def __init__(self, argv: list[str], *, section: str, command: str = "token_command") -> None:
        super().__init__(_MASK)
        self._argv = tuple(argv)
        self._section = section
        self._command = command

    def get_secret_value(self) -> str:
        """Run the command once per process and return its stripped stdout."""
        return read_command(self._argv, setting=f"{self._section}.{self._command}")


def read_command(argv: Sequence[str], *, setting: str) -> str:
    """Run a token command once per process and return its stripped stdout.

    ``setting`` names where the command came from in errors (``awx.token_command``,
    ``the stored awx token``). A relative ``argv[0]`` with a ``/`` is refused
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


def resolve_token[T: BaseModel](settings: T, *, section: str) -> T:
    """Return ``settings`` with its secret settings filled where they are unset.

    For a plugin that declares credentials: each one whose name is still a
    setting of the section (an 11.0 model kept as is, such as github's
    ``token``) is filled from :meth:`~untaped.sdk.network.credentials.Credential.get`,
    or left ``None`` when nothing answers. For an 11.0 section with
    ``token_sources``: its ``token`` from ``token_command``, then its
    variables. A field already set is left as it is. A command is not run
    here; it runs when the secret is first read.
    """
    from untaped.sdk.network.credentials import declared_credentials  # noqa: PLC0415 - imports this

    declared = declared_credentials(section)
    if declared:
        return _fill_declared(settings, declared)
    if isinstance(getattr(type(settings), "token_sources", None), TokenSources):
        return _resolve_legacy(settings, section=section)
    return settings


def _fill_declared[T: BaseModel](settings: T, declared: Sequence[Any]) -> T:
    from untaped.sdk.network.credentials import NoCredential, credential_key  # noqa: PLC0415

    update: dict[str, SecretStr | None] = {}
    for credential in declared:
        if credential.name not in type(settings).model_fields:
            continue
        key = credential_key(credential, settings)
        try:
            update[credential.name] = None if key is None else credential.get(key)
        except NoCredential:
            update[credential.name] = None
    return settings.model_copy(update=update) if update else settings


def _resolve_legacy[T: BaseModel](settings: T, *, section: str) -> T:
    from untaped.sdk.network.credentials import legacy_section_refused  # noqa: PLC0415

    refused = legacy_section_refused(section)
    if _explicit_token(settings):
        if refused:
            return settings.model_copy(update={"token": None})
        _warn_plaintext(settings, section=section)
        return settings
    argv = getattr(settings, "token_command", None)
    if refused:
        return settings
    if argv:
        token: SecretStr = CommandToken(argv, section=section)
    else:
        env_name = _env_source(_legacy_env(type(settings)) or ())
        if env_name is None:
            return settings
        _LOG.debug("%s.token: using $%s", section, env_name)
        token = SecretStr(os.environ[env_name].strip())
    return settings.model_copy(update={"token": token})


def describe_token_source(
    settings: BaseModel, *, section: str, ambient: bool = True, field: str = "token"
) -> str | None:
    """Name where an 11.0 section's ``token`` would come from, without running anything.

    ``ambient=False`` leaves out the conventional environment variables:
    they are not tied to a profile, so alone they do not make a section
    configured. Returns ``None`` when no source is configured, or for any
    other ``field``. For doctor checks.
    """
    if field != "token":
        return None
    if _explicit_token(settings, field):
        return f"{section}.{field}"
    if getattr(settings, "token_command", None):
        return f"{section}.token_command"
    env_name = _env_source(_legacy_env(type(settings)) or ()) if ambient else None
    return None if env_name is None else f"${env_name}"


def secret_source(settings: BaseModel, *, section: str, field: str = "token") -> str | None:
    """Which source an 11.0 section's ``token`` comes from, named without reading it.

    The variable's name when one supplies it (``UNTAPED_ACME__TOKEN``,
    ``ACME_TOKEN``), ``<section>.token`` for a value in the config file,
    ``stored`` for its command; ``None`` when none is configured. For the
    settings digest (:mod:`untaped.sdk.storage._digest`).
    """
    if _explicit_token(settings, field):
        return env_var_setting((section, field)) or f"{section}.{field}"
    if getattr(settings, "token_command", None):
        return "stored"
    return _env_source(_legacy_env(type(settings)) or ())


def token_env_names(settings: BaseModel, *, field: str = "token") -> tuple[str, ...]:
    """The conventional variables an 11.0 ``settings`` model declares for its ``token``."""
    return (_legacy_env(type(settings)) or ()) if field == "token" else ()


def token_alternatives(settings: BaseModel, *, section: str, field: str = "token") -> str:
    """Name the sources that keep an 11.0 section's token out of the config file.

    Its ``<section>.token_command`` (when the model takes one) and its first
    conventional variable, joined with ``or``; empty when it has neither.
    """
    names = [f"{section}.token_command"] if takes_token_command(type(settings)) else []
    names.extend(f"${name}" for name in token_env_names(settings, field=field)[:1])
    return " or ".join(names)


def token_instead(settings: BaseModel, *, section: str, field: str = "token") -> str:
    """What to use instead of a plaintext ``<section>.<field>``.

    :func:`token_alternatives`, or the ``UNTAPED_<SECTION>__<FIELD>`` override
    when the model has neither a command nor a conventional variable.
    """
    alternatives = token_alternatives(settings, section=section, field=field)
    return alternatives or f"${token_override_name(section, field=field)}"


def takes_token_command(model: type[BaseModel]) -> bool:
    """Whether an 11.0 ``model`` declares ``token`` beside ``token_command``."""
    return {"token", "token_command"} <= model.model_fields.keys()


def token_override_name(section: str, *, field: str = "token") -> str:
    """The ``UNTAPED_<SECTION>__<FIELD>`` variable that overrides ``<section>.<field>``."""
    return env_var_name([section, field])


def token_override_env(section: str, *, field: str = "token") -> str | None:
    """The environment variable that sets ``<section>.<field>`` right now, if any.

    ``UNTAPED_<SECTION>__<FIELD>``, or ``UNTAPED_<SECTION>`` when it holds a
    JSON object with that key; either one wins over the config file.
    """
    name = token_override_name(section, field=field)
    if os.environ.get(name, "").strip():
        return name
    whole = env_var_name([section])
    try:
        node = json.loads(os.environ.get(whole) or "null")
    except ValueError:
        return None
    token = node.get(field) if isinstance(node, dict) else None
    return whole if isinstance(token, str) and token.strip() else None


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
    """Forget every ``token_command`` result (tests, embedding)."""
    _cache.clear()


def _warn_plaintext(settings: BaseModel, *, section: str) -> None:
    """Warn once that an 11.0 ``<section>.token`` comes from the config file in plain text.

    A secret from the environment (:func:`token_override_env`) is not a
    file, so it never warns; nor does one ``auth set`` cannot store, since
    ``auth migrate`` could not move it.
    """
    # ``setup`` checks a candidate token that is not written yet, possibly on a
    # worker thread over its screen: nothing to warn about, nowhere to print it.
    if current_overlay() is not None or token_override_env(section) is not None:
        return
    if not takes_token_command(type(settings)):
        return
    from untaped.sdk.words import run_hint  # noqa: PLC0415 - keep auth imports light

    config_name = resolve_config_path().name
    PLAINTEXT_TOKEN.notify(
        plaintext_subject(section, config_name),
        key=section,
        hint=f"{run_hint('auth migrate')} to move it out of {config_name}",
    )


def _explicit_token(settings: BaseModel, field: str = "token") -> bool:
    token = getattr(settings, field, None)
    if isinstance(token, CommandToken):
        return False
    return isinstance(token, SecretStr) and bool(token.get_secret_value().strip())


def _env_source(names: tuple[str, ...]) -> str | None:
    return next((name for name in names if os.environ.get(name, "").strip()), None)


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
    "PLAINTEXT_TOKEN",
    "TRUST_HINT",
    "CommandToken",
    "TokenCommand",
    "TokenSources",
    "check_program",
    "clear_token_cache",
    "command_failure",
    "command_folder",
    "describe_token_source",
    "find_program",
    "first_stderr_line",
    "forget_token_command",
    "gpg_hint",
    "locked_store_error",
    "locked_without_terminal",
    "pass_gpg_env",
    "pass_gpg_options",
    "plaintext_subject",
    "read_command",
    "resolve_token",
    "run_command",
    "takes_token_command",
    "token_alternatives",
    "token_env_names",
    "token_instead",
    "token_override_env",
    "token_override_name",
]
