"""The default ``--format`` (``UNTAPED_FORMAT``, ``untaped.ui.format``) and the stderr theme."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import get_args

from cyclopts import App, Argument, ArgumentCollection, Token

from untaped.runtime.run_output import raise_usage
from untaped.runtime.theme import OutputFormat, resolve_theme_or_default
from untaped.sdk._run import Rendering, fixed_rendering, run_or_none
from untaped.sdk._theme import ThemeSpec
from untaped.sdk.errors import UntapedError

#: Environment variable naming the default ``--format`` (wins over ``untaped.ui.format``).
FORMAT_ENV = "UNTAPED_FORMAT"


def apply_default_format(
    app: App, commands: tuple[str, ...], arguments: ArgumentCollection
) -> None:
    """Cyclopts config source: default an omitted ``--format`` to the run's rendering.

    Installed on the root app. A declared command whose ``Output`` defaults
    to ``table`` takes the default the run's rendering chose (the root fixed
    it before the parse, from ``UNTAPED_FORMAT`` or ``untaped.ui.format``);
    an explicit ``--format`` has tokens already and always wins, and a
    command defaulting to another format keeps it.
    """
    del commands
    rendering = fixed_rendering()
    if rendering is not None and _declared(app):
        _default_to(_table_default_format(arguments), _rendered_default(rendering))


def _note_format(fmt: str | None) -> None:
    """Record the ``--format`` a line asks for on its run's policy, before rendering is fixed.

    An error found before the root fixes the rendering (a bad root option)
    then reports in the format asked for (:func:`note_requested_format`).
    Outside a run there is no invocation to record it for, and under a
    fixed rendering there is nothing left to choose: nothing happens.
    """
    run = run_or_none()
    if run is not None and run.rendering is None:
        run.set_policy(replace(run.policy, format=fmt))


def _rendered_default(rendering: Rendering) -> tuple[str, str] | None:
    """The user's default format the run's rendering chose, and its source; ``None`` for none."""
    fmt, source = rendering.format, rendering.source
    if fmt is None or source is None or source == "--format":
        return None
    return fmt, source


def _default_to(argument: Argument | None, chosen: tuple[str, str] | None) -> None:
    """Give the omitted ``argument`` the ``chosen`` default format, from its source."""
    if argument is not None and chosen is not None:
        value, source = chosen
        argument.append(Token(keyword=source, value=value, source=source))


def user_theme() -> ThemeSpec:
    """The user's ``untaped.ui.theme`` for stderr messages; the default on a broken ``ui``."""
    # Keep settings lazy, as ``user_default_format`` does.
    from untaped.runtime.settings import load_core_part  # noqa: PLC0415

    return resolve_theme_or_default(lambda: load_core_part("ui"), strict=False)


def _declared(app: App) -> bool:
    """Whether the command parsed (``app``) is one the mount made from a declaration."""
    # Lazy: the mount imports this module.
    from untaped.runtime.mount import is_declared  # noqa: PLC0415

    return is_declared(app.default_command)


def _table_default_format(arguments: ArgumentCollection) -> Argument | None:
    """The omitted table-default ``--format`` among ``arguments``, if any.

    That is a ``--format`` whose choices include every shared format (an
    ``Output``'s, with or without formats of the plugin's own); the user's
    default is still one of the shared formats.
    """
    return next(
        (
            argument
            for argument in arguments
            if "--format" in argument.names
            and not argument.tokens
            and follows_user_format(get_args(argument.hint), argument.field_info.default)
        ),
        None,
    )


def follows_user_format(choices: Iterable[object], default: object) -> bool:
    """Whether a ``--format`` offering ``choices`` and defaulting to ``default`` follows the user's.

    It does when it offers every shared format and defaults to ``table``: only
    then do ``UNTAPED_FORMAT`` and ``untaped.ui.format`` set it. The parse
    asks it of the cyclopts argument, the root of the declaration
    (``untaped.runtime.root_options``), so the two never disagree.
    """
    return set(get_args(OutputFormat)) <= set(choices) and default == "table"


def user_default_format(*, repairs: bool) -> tuple[str, str] | None:
    """The user's default for a table-default ``--format``, and where it came from.

    ``UNTAPED_FORMAT``, else the ``untaped.ui.format`` setting; ``None`` when
    neither is set. A ``ui`` section that fails to load is ignored here so
    ``config set`` can still repair it; ``doctor`` reports it. An invalid
    ``UNTAPED_FORMAT`` is a usage error, except under a command marked
    ``repairs`` (``doctor``, ``setup``, ``skills``), which ignores it.
    """
    choices = get_args(OutputFormat)
    value = os.environ.get(FORMAT_ENV) or None
    if value is not None:
        if value in choices:
            return value, FORMAT_ENV
        if not repairs:
            raise_usage(f"{FORMAT_ENV} must be one of {', '.join(choices)}; got {value!r}")
    # Keep settings lazy: only commands printing rows need them here.
    from untaped.runtime.settings import load_core_part  # noqa: PLC0415

    try:
        setting = load_core_part("ui").format
    except UntapedError:
        return None
    return None if setting is None else (setting, "untaped.ui.format")


def requested_format(tokens: Sequence[str]) -> str | None:
    """The value of the last ``--format``/``-f`` before ``--`` in raw ``tokens``; ``None``."""
    value = None
    for index, token in enumerate(tokens):
        if token == "--":
            break
        name, separator, inline = token.partition("=")
        if name in ("--format", "-f"):
            if separator:
                value = inline
            elif index + 1 < len(tokens):
                value = tokens[index + 1]
    return value


def note_requested_format(tokens: Sequence[str]) -> None:
    """Record the ``--format`` raw ``tokens`` ask for, for an error found before parsing.

    The last ``--format``/``-f`` before ``--`` (:func:`requested_format`)
    wins over ``UNTAPED_FORMAT``; only a structured value switches the error
    to a JSON line.
    """
    _note_format(requested_format(tokens) or os.environ.get(FORMAT_ENV) or None)
