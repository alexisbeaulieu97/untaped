"""Token resolution for token-bearing capability settings sections.

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

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, BaseModel, SecretStr

from untaped.errors import ConfigError

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
    names = [f"{section}.token_command"] if "token_command" in type(settings).model_fields else []
    names.extend(f"${name}" for name in token_env_names(settings)[:1])
    return " or ".join(names)


def clear_token_cache() -> None:
    """Forget every ``token_command`` result and plaintext warning (tests, embedding)."""
    _cache.clear()
    _warned.clear()


def _warn_plaintext(settings: BaseModel, *, section: str) -> None:
    """Warn once that ``<section>.token`` comes from the config file in plain text.

    An ``UNTAPED_<SECTION>__TOKEN`` (or ``UNTAPED_<SECTION>``) override is not
    a file, so it never warns; nor does a model ``auth set`` cannot serve.
    """
    prefix = f"UNTAPED_{section.upper()}"
    if (
        section in _warned
        or "token_command" not in type(settings).model_fields
        or any(os.environ.get(name) for name in (prefix, f"{prefix}__TOKEN"))
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


def _run_token_command(argv: tuple[str, ...], *, section: str) -> str:
    key = f"{section}.token_command"
    program = argv[0]
    _LOG.debug("%s: running %s", key, program)
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            # stderr goes straight to the terminal: the command explains its
            # own failures, and untaped never captures or repeats them.
            stdout=subprocess.PIPE,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise ConfigError(f"{key}: {program!r} not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise ConfigError(
            f"{key}: {program!r} timed out after {_TIMEOUT_SECONDS:g}s", category="unavailable"
        ) from None
    except OSError as exc:
        raise ConfigError(f"{key}: {program!r} could not run: {exc.strerror}") from None
    if completed.returncode != 0:
        raise ConfigError(f"{key}: {program!r} exited with status {completed.returncode}")
    token = completed.stdout.strip()
    if not token:
        raise ConfigError(f"{key}: {program!r} printed no token")
    return token


__all__ = [
    "CommandToken",
    "TokenCommand",
    "TokenSources",
    "clear_token_cache",
    "describe_token_source",
    "resolve_token",
    "token_alternatives",
    "token_env_names",
]
