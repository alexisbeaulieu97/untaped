"""Stability in help: the Experimental and Deprecated panels, each marked app's group and last line.

The root shell calls :func:`mark_app` and :func:`apply_marks` wherever it mounts commands;
``--deprecated`` (:func:`show_deprecated`) shows the Deprecated panels.
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from cyclopts import App, Group

from untaped.sdk._parameter_marks import parameter_marks
from untaped.sdk._replacement import children, replacement_text
from untaped.sdk._run import run_or_none
from untaped.sdk.marks import (
    _ROOT_NAME,
    Deprecated,
    Experimental,
    Stability,
    function_mark,
    path_owner,
    removal_text,
)
from untaped.sdk.words import (
    EXPERIMENTAL_LINE,
    deprecated_line,
)


def show_deprecated() -> bool:
    """Whether the current run asked for ``--deprecated``; ``False`` outside a run.

    The root option is the run's (its policy), so it ends with the run.
    """
    run = run_or_none()
    return run is not None and run.policy.deprecated


class _DeprecatedGroup(Group):
    """The Deprecated panel: listed only while ``--deprecated`` is in effect."""

    @property
    def show(self) -> bool:
        return show_deprecated()


# The command whose help is being printed, as the root's token rewrite selected it, with the
# root: the Deprecated options panel reads its marks to write each option's line.
_SELECTED_COMMAND: ContextVar[tuple[App, App, tuple[str, ...]] | None] = ContextVar(
    "untaped_selected_command", default=None
)


@contextmanager
def command_selection(root: App, command: App, path: tuple[str, ...]) -> Iterator[None]:
    """``command`` (at ``path``, ``("untaped", "awx", ...)``) is this invocation's inside the block.

    The only way to select one, so a selection never outlives its invocation.
    """
    token = _SELECTED_COMMAND.set((root, command, path))
    try:
        yield
    finally:
        _SELECTED_COMMAND.reset(token)


def _deprecated_options_help(console: Any, options: Any, panel: Any) -> None:
    """The Deprecated options panel: each option's help is its mark's ``Deprecated:`` line."""
    from cyclopts.help import DefaultFormatter  # noqa: PLC0415 - help only

    selected = _SELECTED_COMMAND.get()
    lines: dict[str, str] = {}
    if selected is not None:
        root, command, path = selected
        for argument, mark in marked_options(command):
            removal = removal_text(mark.removed_in, path_owner(path))
            line = deprecated_line(replacement_text(mark, root), removal)
            # An entry's description is plain text, not markdown, so no backticks.
            lines.update(dict.fromkeys(argument.names, line.replace("`", "")))
    entries = []
    for entry in panel.entries:
        found = next((lines[name] for name in entry.all_options if name in lines), None)
        entries.append(entry if found is None else entry.copy(description=found))
    DefaultFormatter()(console, options, panel.copy(entries=entries))


# The order of all core panels is untaped.runtime.help_panels'.
EXPERIMENTAL_GROUP = Group("Experimental", sort_key=100)


DEPRECATED_GROUP = _DeprecatedGroup("Deprecated", sort_key=200)


DEPRECATED_OPTIONS = _DeprecatedGroup(
    "Deprecated options", sort_key=250, help_formatter=_deprecated_options_help
)
"""The panel of a command's deprecated options, listed only under ``--deprecated``.

Mount puts a flag there from the ``deprecated(...)`` mark on its field; the
root warns when any of its spellings is used.
"""


def marked_options(command: App) -> list[tuple[Any, Deprecated]]:
    """Each option of ``command``'s function marked ``deprecated(...)``, with its mark.

    Only a keyword-only parameter with its own spellings is an option: a mark on a positional
    or a flattened dataclass is not listed.
    """
    function = command.default_command
    if function is None:
        return []
    marks = parameter_marks(function)
    if not marks:
        return []
    return [
        (argument, marks[argument.field_info.name])
        for argument in command.assemble_argument_collection(parse_docstring=False)
        if argument.field_info.name in marks
        and argument.field_info.kind is inspect.Parameter.KEYWORD_ONLY
        and not argument.children
    ]


def option_name(argument: Any) -> str:
    """The spelling an option goes under: its first long positive name, else its first one."""
    positive: list[str] = [name for name in argument.names if name not in argument.negatives]
    return next((name for name in positive if name.startswith("--")), positive[0])


def panel_for(stability: Stability) -> Group:
    """The core-owned help panel a mark places a command in."""
    return EXPERIMENTAL_GROUP if isinstance(stability, Experimental) else DEPRECATED_GROUP


@dataclass(frozen=True)
class AppMark:
    """A mark on an app. ``source`` tells the author's own mark from one core applied."""

    stability: Stability
    source: Literal["own", "plugin"]


# Keyed by ``id(app)``: cyclopts apps are unhashable. A finalizer drops the
# entry with the app, so a recycled id never inherits a stale mark.
_APP_MARKS: dict[int, AppMark] = {}


def _forget_with(app: App, table: dict[int, Any]) -> None:
    weakref.finalize(app, table.pop, id(app), None)


def app_mark(app: App) -> AppMark | None:
    """The mark the mount registered on ``app``."""
    return _APP_MARKS.get(id(app))


def mark_of(app: App) -> Stability | None:
    """The stability of ``app``: its registered mark, else the mark on its command function."""
    entry = _APP_MARKS.get(id(app))
    return entry.stability if entry is not None else function_mark(app.default_command)


def mark_app(
    app: App,
    stability: Stability,
    *,
    source: Literal["own", "plugin"],
    tree: App | None = None,
    prefix: tuple[str, ...] = (_ROOT_NAME,),
) -> None:
    """Register ``stability`` on ``app``: side table, panel and help line.

    Idempotent. The panel replaces the app's groups, so a marked command shows
    only in its stability panel. A ``"plugin"`` mark (its plugin's, which core
    applies) never replaces an ``"own"`` one. An object replacement
    can only be named against a ``tree`` (``prefix`` is where ``app``'s tree
    hangs from the root); without one the help line waits for
    :func:`apply_marks`.
    """
    key = id(app)
    existing = _APP_MARKS.get(key)
    if existing is None:
        _forget_with(app, _APP_MARKS)
    if existing is None or existing.source != "own" or source == "own":
        _APP_MARKS[key] = AppMark(stability, source)
    effective = _APP_MARKS[key].stability
    app.group = (panel_for(effective),)
    line = stability_line(effective, tree, prefix)
    if line is not None:
        _ensure_epilogue(app, line, own_only=False)


def _ensure_epilogue(app: App, line: str, *, own_only: bool) -> None:
    """End ``app``'s help epilogue with ``line`` (once); ``own_only`` spares an app without one.

    A stability line written before the app was mounted (when its owner, and
    so its removal version's wording, was not known yet) is replaced, never
    kept beside the new one. The docs footer (:func:`docs_footer`) stays last.
    """
    epilogue = app.help_epilogue
    if epilogue is None:
        if not own_only:
            app.help_epilogue = line
        return
    if line in epilogue:
        return
    paragraphs = [
        paragraph
        for paragraph in epilogue.rstrip().split("\n\n")
        if not _is_stability_line(paragraph)
    ]
    footer = [paragraph for paragraph in paragraphs if paragraph.startswith(FOOTER_START)]
    rest = [paragraph for paragraph in paragraphs if not paragraph.startswith(FOOTER_START)]
    app.help_epilogue = "\n\n".join([*rest, line, *footer])


#: How the docs footer opens: every ``--help`` ends with the address of its page.
FOOTER_START = "Full documentation: "


def docs_footer(app: App, address: str, *, keep: bool = False) -> None:
    """End ``app``'s help with "Full documentation: `ADDRESS`", the page that documents it.

    It replaces a footer already there, unless ``keep`` (a plugin's app keeps
    the footers its commands were built with); a stability line stays above it.
    """
    epilogue = app.help_epilogue or ""
    paragraphs = [paragraph for paragraph in epilogue.rstrip().split("\n\n") if paragraph]
    if keep and any(paragraph.startswith(FOOTER_START) for paragraph in paragraphs):
        return
    rest = [paragraph for paragraph in paragraphs if not paragraph.startswith(FOOTER_START)]
    app.help_epilogue = "\n\n".join([*rest, f"{FOOTER_START}`{address}`"])


def _is_stability_line(paragraph: str) -> bool:
    return paragraph == EXPERIMENTAL_LINE or paragraph.startswith(_DEPRECATED_LINE_START)


_DEPRECATED_LINE_START = deprecated_line().partition(" removed in ")[0] + " removed in "


def stability_line(
    stability: Stability, tree: App | None, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> str | None:
    """The help line for ``stability``; ``None`` while an object replacement has no ``tree``.

    A deprecated line names the removal version of the plugin that owns
    ``prefix`` (``removed in untaped-awx 12.0``), or core's bare version.
    """
    if isinstance(stability, Experimental):
        return EXPERIMENTAL_LINE
    if callable(stability.replacement) and tree is None:
        return None
    removal = removal_text(stability.removed_in, path_owner(prefix))
    return deprecated_line(replacement_text(stability, tree, prefix), removal)


def apply_marks(app: App, *, path: tuple[str, ...] = ()) -> None:
    """Give every marked command under ``app`` its panel and help line (idempotent).

    ``path`` is where ``app`` hangs from the root (``()`` for the root,
    ``(name,)`` for a mounted plugin or root command). It sets the group
    and epilogue on the wrapper app cyclopts builds for each marked command
    function, and makes every app inside a marked subtree that has an epilogue
    of its own end with the stability line, so an author epilogue never hides
    it. It never resolves a lazy plugin.
    """
    _apply(app, app, (_ROOT_NAME, *path), None, set())


def _apply(
    node: App, tree: App, prefix: tuple[str, ...], inherited: str | None, visited: set[int]
) -> None:
    if id(node) in visited:
        return
    visited.add(id(node))
    mark = mark_of(node)
    if mark is not None:
        entry = app_mark(node)
        mark_app(node, mark, source=entry.source if entry else "own", tree=tree, prefix=prefix)
        inherited = stability_line(mark, tree, prefix)
    elif inherited is not None:
        _ensure_epilogue(node, inherited, own_only=True)
    for _, sub in children(node, resolve=False):
        _apply(sub, tree, prefix, inherited, visited)
