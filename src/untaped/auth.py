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
    if not isinstance(sources, TokenSources) or _explicit_token(settings):
        return settings
    argv = getattr(settings, "token_command", None)
    if argv:
        token: SecretStr = CommandToken(argv, section=section)
    else:
        env_name = _env_source(sources)
        if env_name is None:
            return settings
        _LOG.debug("%s.token: using $%s", section, env_name)
        token = SecretStr(os.environ[env_name].strip())
    return settings.model_copy(update={"token": token})


def describe_token_source(settings: BaseModel, *, section: str) -> str | None:
    """Name where the token would come from, without running anything.

    Returns ``None`` when no source is configured. For doctor checks.
    """
    if _explicit_token(settings):
        return f"{section}.token"
    if getattr(settings, "token_command", None):
        return f"{section}.token_command"
    sources = getattr(type(settings), "token_sources", None)
    if isinstance(sources, TokenSources):
        env_name = _env_source(sources)
        if env_name is not None:
            return f"${env_name}"
    return None


def clear_token_cache() -> None:
    """Forget every ``token_command`` result (tests and embedding callers)."""
    _cache.clear()


def _explicit_token(settings: BaseModel) -> bool:
    token = getattr(settings, "token", None)
    if isinstance(token, CommandToken):
        return False
    return isinstance(token, SecretStr) and bool(token.get_secret_value().strip())


def _env_source(sources: TokenSources) -> str | None:
    return next((name for name in sources.env if os.environ.get(name, "").strip()), None)


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
        raise ConfigError(f"{key}: {program!r} timed out after {_TIMEOUT_SECONDS:g}s") from None
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
]
