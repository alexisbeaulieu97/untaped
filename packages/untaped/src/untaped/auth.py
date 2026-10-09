"""Token resolution for token-bearing plugin settings sections.

One place owns where a section's API token comes from when ``<section>.token``
is unset. A settings model opts in by declaring ``token_sources:
ClassVar[TokenSources]`` and a ``token_command: TokenCommand`` field.
Precedence, first match wins:

1. ``<section>.token`` (config file or its ``UNTAPED_<SECTION>__TOKEN``
   override);
2. ``<section>.token_command``: an argv list run without a shell, at most
   once per process and only when the token is first read; its stripped
   stdout is the token;
3. the section's conventional environment variables, in declared order
   (for example ``GH_TOKEN`` then ``GITHUB_TOKEN``).

Token values never reach logs or error messages: a failing command is
reported by program name and exit status only, never by its arguments or
stdout. Its stderr goes straight to the terminal, uncaptured.

A token stored in plain text in the config file is deprecated: using one
warns once per section per process and points at ``untaped auth migrate``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, SecretStr

from untaped.errors import ConfigError
from untaped.settings import env_var_name

_LOG = logging.getLogger("untaped.auth")
_TIMEOUT_SECONDS = 60.0
_MASK = "**********"
_cache: dict[tuple[str, ...], str] = {}
_warned: set[str] = set()


def _check_argv(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not value or not value[0].strip():
        raise ValueError("token_command must be a non-empty argv list, e.g. [gh, auth, token]")
    return value


TokenCommand = Annotated[list[str] | None, AfterValidator(_check_argv)]
"""Field type for ``<section>.token_command``: an argv list, run without a shell."""


@dataclass(frozen=True)
class TokenSources:
    """Where a settings section looks for its token when ``token`` is unset.

    ``env`` names conventional environment variables tried after
    ``token_command``, in order.
    """

    env: tuple[str, ...] = ()


class CommandToken(SecretStr):
    """A token read from ``token_command`` stdout on first use, then cached.

    Displays masked like any :class:`SecretStr` and never runs the command
    for ``str``/``repr``, so an unused token costs nothing.
    """

    def __init__(self, argv: list[str], *, section: str) -> None:
        super().__init__(_MASK)
        self._argv = tuple(argv)
        self._section = section

    def get_secret_value(self) -> str:
        """Run the command once per process and return its stripped stdout."""
        cached = _cache.get(self._argv)
        if cached is None:
            cached = _run_token_command(self._argv, section=self._section)
            _cache[self._argv] = cached
        return cached


def resolve_token[T: BaseModel](settings: T, *, section: str) -> T:
    """Return ``settings`` with its token filled from the fallback sources.

    Models without a ``token_sources`` declaration, and models whose
    ``token`` is already set, are returned unchanged. ``token_command`` is
    not run here; it runs when the token is first read.
    """
    sources = getattr(type(settings), "token_sources", None)
    if not isinstance(sources, TokenSources):
        return settings
    if _explicit_token(settings):
        _warn_plaintext(settings, section=section)
        return settings
    argv = getattr(settings, "token_command", None)
    if argv:
        token: SecretStr = CommandToken(argv, section=section)
    else:
        env_name = _env_source(sources.env)
        if env_name is None:
            return settings
        _LOG.debug("%s.token: using $%s", section, env_name)
        token = SecretStr(os.environ[env_name].strip())
    return settings.model_copy(update={"token": token})


def describe_token_source(settings: BaseModel, *, section: str, ambient: bool = True) -> str | None:
    """Name where the token would come from, without running anything.

    ``ambient=False`` leaves out the conventional environment variables:
    they are not tied to a profile, so alone they do not make a section
    configured. Returns ``None`` when no source is configured. For doctor
    checks.
    """
    if _explicit_token(settings):
        return f"{section}.token"
    if getattr(settings, "token_command", None):
        return f"{section}.token_command"
    env_name = _env_source(token_env_names(settings)) if ambient else None
    return None if env_name is None else f"${env_name}"


def token_env_names(settings: BaseModel) -> tuple[str, ...]:
    """The conventional token variables ``settings``' model declares, in order."""
    sources = getattr(type(settings), "token_sources", None)
    return sources.env if isinstance(sources, TokenSources) else ()


def token_alternatives(settings: BaseModel, *, section: str) -> str:
    """Name the token sources that keep a token out of the config file.

    ``<section>.token_command`` (when the model has it) and the first
    conventional variable, joined with ``or``; empty when the model has
    neither.
    """
    names = [f"{section}.token_command"] if takes_token_command(type(settings)) else []
    names.extend(f"${name}" for name in token_env_names(settings)[:1])
    return " or ".join(names)


def token_instead(settings: BaseModel, *, section: str) -> str:
    """What to use instead of a plaintext ``<section>.token``.

    :func:`token_alternatives`, or the ``UNTAPED_<SECTION>__TOKEN`` override
    when the model has neither a command nor a conventional variable.
    """
    return token_alternatives(settings, section=section) or f"${token_override_name(section)}"


def takes_token_command(model: type[BaseModel]) -> bool:
    """Whether ``model`` has a ``token_command`` field (so ``auth set`` serves it)."""
    return "token_command" in model.model_fields


def token_override_name(section: str) -> str:
    """The ``UNTAPED_<SECTION>__TOKEN`` variable that overrides ``<section>.token``."""
    return env_var_name([section, "token"])


def token_override_env(section: str) -> str | None:
    """The environment variable that sets ``<section>.token`` right now, if any.

    ``UNTAPED_<SECTION>__TOKEN``, or ``UNTAPED_<SECTION>`` when it holds a
    JSON object with a ``token``; either one wins over the config file.
    """
    name = token_override_name(section)
    if os.environ.get(name, "").strip():
        return name
    whole = name.removesuffix("__TOKEN")
    try:
        node = json.loads(os.environ.get(whole) or "null")
    except ValueError:
        return None
    token = node.get("token") if isinstance(node, dict) else None
    return whole if isinstance(token, str) and token.strip() else None


def run_command(
    argv: Sequence[str],
    *,
    label: str,
    stdin: str | None = None,
    capture_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` without a shell, its stdout captured, and return it finished.

    ``label`` starts every error, which never repeats the command's output or
    arguments. stderr goes straight to the terminal unless ``capture_stderr``.
    A non-zero exit is returned, not raised.
    """
    try:
        return subprocess.run(
            list(argv),
            input=stdin or "",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else None,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise ConfigError(f"{label} not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise ConfigError(
            f"{label} timed out after {_TIMEOUT_SECONDS:g}s; it may be waiting on an "
            "unlock prompt on a screen nobody sees (over SSH, unlock the store first)",
            category="unavailable",
        ) from None
    except OSError as exc:
        raise ConfigError(f"{label} could not run: {exc.strerror}") from None


def forget_token_command(argv: list[str]) -> None:
    """Forget the cached result of ``argv``, so its next read runs the command again.

    For a caller that checks a token it was just given (``setup``): a retry after a rejected
    token must run the command again, not read the rejected result back.
    """
    _cache.pop(tuple(argv), None)


def clear_token_cache() -> None:
    """Forget every ``token_command`` result and plaintext warning (tests, embedding)."""
    _cache.clear()
    _warned.clear()


def _warn_plaintext(settings: BaseModel, *, section: str) -> None:
    """Warn once that ``<section>.token`` comes from the config file in plain text.

    A token from the environment (:func:`token_override_env`) is not a file,
    so it never warns; nor does a model ``auth set`` cannot serve.
    """
    from untaped.settings import active_overlay  # noqa: PLC0415 - imports each other lazily

    # ``setup`` checks a candidate token that is not written yet, possibly on a
    # worker thread over its screen: nothing to warn about, nowhere to print it.
    if active_overlay() is not None:
        return
    if (
        section in _warned
        or not takes_token_command(type(settings))
        or token_override_env(section) is not None
    ):
        return
    _warned.add(section)
    from untaped.messages import hint  # noqa: PLC0415 - keep auth imports light
    from untaped.ui import ui_context  # noqa: PLC0415

    ui_context(strict=False).message(
        "warning",
        f"{section}.token is stored in plain text in the config file, which is deprecated\n"
        f"{hint('auth migrate')}",
    )


def _explicit_token(settings: BaseModel) -> bool:
    token = getattr(settings, "token", None)
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


def _run_token_command(argv: tuple[str, ...], *, section: str) -> str:
    label = f"{section}.token_command: {argv[0]!r}"
    _LOG.debug("%s.token_command: running %s", section, argv[0])
    # stderr goes straight to the terminal: the command explains its own
    # failures, and untaped never captures or repeats them. Except `pass`,
    # whose gpg repeats one error per call: the first line and one hint do.
    is_pass = Path(argv[0]).name == "pass"
    completed = run_command(argv, label=label, capture_stderr=is_pass)
    if completed.returncode != 0:
        message = f"{label} exited with status {completed.returncode}"
        if is_pass:
            if quote := first_stderr_line(completed.stderr):
                message += f": {quote}"
            raise ConfigError(message, hint=gpg_hint(completed.stderr))
        raise ConfigError(message)
    token = completed.stdout.strip()
    if not token:
        raise ConfigError(f"{label} printed no token")
    return token


__all__ = [
    "PASS_GPG_HINT",
    "TRUST_HINT",
    "CommandToken",
    "TokenCommand",
    "TokenSources",
    "clear_token_cache",
    "describe_token_source",
    "first_stderr_line",
    "forget_token_command",
    "gpg_hint",
    "resolve_token",
    "run_command",
    "takes_token_command",
    "token_alternatives",
    "token_env_names",
    "token_instead",
    "token_override_env",
    "token_override_name",
]
