"""Stability marks: which commands are experimental or deprecated, and what core does about it.

Every stability fact about a command lives here. The author marks the object
once (``@experimental`` or ``@deprecated(replacement=...)`` on a command
function, ``create_app(stability=...)`` on a group, ``stability=`` on a
:class:`~untaped.capabilities.registry.CapabilitySpec` for a whole
capability), and core supplies the panel in the parent's ``--help``, the last
help line, the run-time warning and the conventions checks:

- the markers and their validation (:func:`check_stability`);
- the Experimental and Deprecated panels, and the ``--deprecated`` flag that
  shows the latter (:func:`show_deprecated`);
- :func:`mark_app` and :func:`apply_marks`, which put a mark's group and help
  line on an app (the root shell calls them wherever it mounts commands);
- :func:`replacement_path`, which finds a replacement command in the mounted
  tree so a warning keeps naming it after a rename;
- :func:`marks`, the one query every checker and generator reads;
- the renamed-command and renamed-option spellings of
  :func:`deprecated_alias`, a different mechanism with a successor.

Neither marker is, or subclasses, ``warnings.deprecated``: that one warns on
every use and, on a settings field, becomes pydantic field deprecation.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cyclopts import App, Group
from cyclopts.command_spec import CommandSpec

from untaped.messages import EXPERIMENTAL_LINE, deprecated_line

if TYPE_CHECKING:
    from untaped.capabilities.registry import CompositionResult

_ROOT_NAME = "untaped"
_FUNCTION_ATTR = "__untaped_stability__"


@dataclass(frozen=True)
class Experimental:
    """The ``experimental`` marker: may change or go away in a minor release."""

    def __call__[F: Callable[..., Any]](self, target: F, /) -> F:
        """Mark a command function and return it unchanged (apply under ``@app.command``)."""
        setattr(target, _FUNCTION_ATTR, self)
        return target


@dataclass(frozen=True)
class Deprecated:
    """The ``deprecated`` marker: still works, with a warning, until the next major release.

    ``replacement`` is a command function or :class:`~cyclopts.App` (untaped
    derives its path), or text: ``"untaped github sync"`` or a setting key
    (shown in backticks) or prose (shown as is).
    """

    replacement: Callable[..., Any] | App | str | None = None

    def __post_init__(self) -> None:
        replacement = self.replacement
        if replacement is None or callable(replacement):
            return
        if not isinstance(replacement, str) or not replacement.strip():
            raise TypeError(
                "deprecated(replacement=...) takes a command function, an App or non-empty "
                f"text, not {replacement!r}"
            )

    def __call__[F: Callable[..., Any]](self, target: F, /) -> F:
        """Mark a command function and return it unchanged (apply under ``@app.command``)."""
        setattr(target, _FUNCTION_ATTR, self)
        return target


experimental = Experimental()
"""Mark a command, group or capability experimental (``@experimental``, ``stability=``)."""


def deprecated(*, replacement: Callable[..., Any] | App | str | None = None) -> Deprecated:
    """Mark a command, group or capability deprecated, naming what replaces it.

    ``replacement`` is keyword-only, so a bare ``@deprecated`` fails at import.
    Use it as ``@deprecated(replacement=set_command)`` under ``@app.command``
    or as ``stability=deprecated(...)``.
    """
    return Deprecated(replacement=replacement)


type Stability = Experimental | Deprecated


def check_stability(value: object, *, where: str) -> Stability | None:
    """``value`` when it is ``None``, ``experimental`` or a ``deprecated(...)`` marker.

    Anything else raises ``TypeError`` naming ``where``: the two likely
    mistakes are ``deprecated`` without its call and Python's own
    ``warnings.deprecated("...")``.
    """
    if value is None or isinstance(value, Experimental | Deprecated):
        return value
    hint = (
        "call it: deprecated(replacement=...)"
        if value is deprecated
        else "use untaped.sdk.experimental or untaped.sdk.deprecated(replacement=...), "
        "not warnings.deprecated"
    )
    raise TypeError(f"{where}: stability must be experimental or deprecated(...); {hint}")


def function_mark(func: object) -> Stability | None:
    """The marker ``@experimental`` or ``@deprecated(...)`` put on a command function."""
    mark = getattr(func, _FUNCTION_ATTR, None)
    return mark if isinstance(mark, Experimental | Deprecated) else None


# --- panels and the --deprecated flag ----------------------------------------

_SHOW_DEPRECATED: ContextVar[bool] = ContextVar("untaped_show_deprecated", default=False)


def show_deprecated() -> bool:
    """Whether ``--deprecated`` is in effect for this run."""
    return _SHOW_DEPRECATED.get()


def enable_show_deprecated(_value: object = None) -> Token[bool]:
    """Show deprecated things for this run (the ``--deprecated`` root option)."""
    return _SHOW_DEPRECATED.set(True)


def reset_show_deprecated(token: Token[bool]) -> None:
    """Undo :func:`enable_show_deprecated`."""
    _SHOW_DEPRECATED.reset(token)


class _DeprecatedGroup(Group):
    """The Deprecated panel: listed only while ``--deprecated`` is in effect."""

    @property
    def show(self) -> bool:
        return show_deprecated()


# Sort keys leave 0-99 to other panels; cyclopts sorts its unkeyed default
# panels before any keyed one, so the root Parameters panel is keyed last.
EXPERIMENTAL_GROUP = Group("Experimental", sort_key=100)
DEPRECATED_GROUP = _DeprecatedGroup("Deprecated", sort_key=200)
ROOT_PARAMETERS_GROUP = Group("Parameters", sort_key=300)
RESERVED_PANELS = frozenset({"Experimental", "Deprecated"})


def panel_for(stability: Stability) -> Group:
    """The core-owned help panel a mark places a command in."""
    return EXPERIMENTAL_GROUP if isinstance(stability, Experimental) else DEPRECATED_GROUP


# --- marks on apps -----------------------------------------------------------


@dataclass(frozen=True)
class AppMark:
    """A mark on an app. ``source`` tells the author's own mark from one core applied."""

    stability: Stability
    source: Literal["own", "spec"]


# Keyed by ``id(app)``: cyclopts apps are unhashable. A finalizer drops the
# entry with the app, so a recycled id never inherits a stale mark.
_APP_MARKS: dict[int, AppMark] = {}
_DEPRECATED_ALIASES: dict[int, dict[str, str]] = {}


def _forget_with(app: App, table: dict[int, Any]) -> None:
    weakref.finalize(app, table.pop, id(app), None)


def app_mark(app: App) -> AppMark | None:
    """The mark core or ``create_app`` registered on ``app``."""
    return _APP_MARKS.get(id(app))


def mark_of(app: App) -> Stability | None:
    """The stability of ``app``: its registered mark, else the mark on its command function."""
    entry = _APP_MARKS.get(id(app))
    return entry.stability if entry is not None else function_mark(app.default_command)


def mark_app(
    app: App,
    stability: Stability,
    *,
    source: Literal["own", "spec"],
    tree: App | None = None,
    prefix: tuple[str, ...] = (_ROOT_NAME,),
) -> None:
    """Register ``stability`` on ``app``: side table, panel and help line.

    Idempotent. The panel replaces the app's groups, so a marked command shows
    only in its stability panel. A ``"spec"`` mark never replaces an ``"own"``
    one, so ``mark-on-spec`` can still see the author's. An object replacement
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
    """End ``app``'s help epilogue with ``line`` (once); ``own_only`` spares an app without one."""
    epilogue = app.help_epilogue
    if epilogue is None:
        if not own_only:
            app.help_epilogue = line
    elif line not in epilogue:
        app.help_epilogue = f"{epilogue.rstrip()}\n\n{line}"


# --- replacements ------------------------------------------------------------

_COMMAND_TEXT = re.compile(r"^untaped(?: [a-z0-9][a-z0-9-]*)+$")
_KEY_TEXT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


def _children(app: App, *, resolve: bool) -> Iterator[tuple[str, App]]:
    """The mounted subcommands of ``app``, the way cyclopts' ``groups_from_app`` walks them.

    An unresolved lazy command is skipped unless ``resolve`` (reading it would
    import its capability). A sub-app mounted under two names appears once.
    """
    seen: set[int] = set()
    for name in app:
        if name.startswith("-"):
            continue
        item = app._get_item(name, recurse_meta=True)
        if isinstance(item, CommandSpec) and not item.is_resolved and not resolve:
            continue
        sub = app[name]
        if id(sub) not in seen:
            seen.add(id(sub))
            yield name, sub


def replacement_path(
    tree: App, replacement: object, *, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> tuple[str, ...] | None:
    """The command path of ``replacement`` (a command function or app) within ``tree``.

    Depth-first over the resolved commands; ``prefix`` is where ``tree`` hangs
    from the root. ``None`` when the object is not mounted.
    """
    return _find(tree, replacement, prefix, set())


def _find(
    app: App, replacement: object, path: tuple[str, ...], visited: set[int]
) -> tuple[str, ...] | None:
    if id(app) in visited:
        return None
    visited.add(id(app))
    for name, sub in _children(app, resolve=False):
        here = (*path, name)
        if sub is replacement or (
            sub.default_command is not None and sub.default_command is replacement
        ):
            return here
        found = _find(sub, replacement, here, visited)
        if found is not None:
            return found
    return None


def replacement_text(
    mark: Deprecated, tree: App | None, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> str | None:
    """The replacement as a help or warning shows it, or ``None`` when there is none.

    An object becomes `` `untaped <path>` ``; a text shaped like a command or a
    setting key is backticked; prose stays as is. An object that is not found
    in ``tree`` (or no ``tree``) gives ``None``.
    """
    replacement = mark.replacement
    if replacement is None:
        return None
    if isinstance(replacement, str):
        return (
            f"`{replacement}`"
            if _COMMAND_TEXT.match(replacement) or _KEY_TEXT.match(replacement)
            else replacement
        )
    path = None if tree is None else replacement_path(tree, replacement, prefix=prefix)
    return None if path is None else f"`{' '.join(path)}`"


def stability_line(
    stability: Stability, tree: App | None, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> str | None:
    """The help line for ``stability``; ``None`` while an object replacement has no ``tree``."""
    if isinstance(stability, Experimental):
        return EXPERIMENTAL_LINE
    if callable(stability.replacement) and tree is None:
        return None
    return deprecated_line(replacement_text(stability, tree, prefix))


# --- applying marks to a tree ------------------------------------------------


def apply_marks(app: App, *, path: tuple[str, ...] = ()) -> None:
    """Give every marked command under ``app`` its panel and help line (idempotent).

    ``path`` is where ``app`` hangs from the root (``()`` for the root,
    ``(name,)`` for a mounted capability or root command). It sets the group
    and epilogue on the wrapper app cyclopts builds for each marked command
    function, and makes every app inside a marked subtree that has an epilogue
    of its own end with the stability line, so an author epilogue never hides
    it. It never resolves a lazy capability.
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
    for _, sub in _children(node, resolve=False):
        _apply(sub, tree, prefix, inherited, visited)


# --- the query ---------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    """One mark: where it sits, what it marks, and what it says."""

    where: str
    """The command path (``awx test``) or capability name."""
    target: Literal["capability", "group", "command"]
    stability: Stability
    replacement: str | None
    """The replacement as help shows it, or ``None``."""


def marks(root: App, result: CompositionResult, *, resolve: bool = False) -> list[Mark]:
    """Every mark in the composition: capability specs, groups and commands.

    Takes the composition, not only the app, because a spec mark is not on a
    lazy app. ``resolve=True`` imports every lazy capability first (tests and
    generators only). Holds no state outside its arguments.
    """
    found: list[Mark] = []
    for capability in result.capabilities:
        spec = capability.spec
        if spec.stability is not None:
            found.append(_record(spec.name, "capability", spec.stability, root))
    capabilities = frozenset(capability.spec.name for capability in result.capabilities)
    _collect(root, root, (), capabilities, found, resolve=resolve)
    return found


def _record(where: str, target: Any, stability: Stability, root: App) -> Mark:
    text = replacement_text(stability, root) if isinstance(stability, Deprecated) else None
    return Mark(where=where, target=target, stability=stability, replacement=text)


def _collect(
    app: App,
    root: App,
    path: tuple[str, ...],
    capabilities: frozenset[str],
    found: list[Mark],
    *,
    resolve: bool,
) -> None:
    for name, sub in _children(app, resolve=resolve):
        here = (*path, name)
        mark = mark_of(sub)
        entry = app_mark(sub)
        if mark is not None and not (entry is not None and entry.source == "spec"):
            if not path:
                target = "capability" if name in capabilities else "command"
            else:
                target = "group" if any(True for _ in _children(sub, resolve=False)) else "command"
            found.append(_record(" ".join(here), target, mark, root))
        _collect(sub, root, here, capabilities, found, resolve=resolve)


# --- renamed commands and options -------------------------------------------


def deprecated_alias(app: App, old: str, new: str) -> None:
    """Keep ``old`` working as a hidden, deprecated spelling of ``new`` on ``app``.

    For a renamed command or group, ``app`` is its parent and the names are
    command names (``deprecated_alias(jira_app, "me", "whoami")``). For a
    renamed option or short flag, ``app`` is the command itself and the names
    are flags (``deprecated_alias(logs_app, "-f", "--follow")``). The root
    shell rewrites the old token to the new one before dispatch and prints a
    warning naming both on stderr, so the old spelling
    never appears in ``--help``. Aliases apply to invocations through the
    ``untaped`` root (test them with ``build_root_app``); they are removed in
    the next major release.
    """
    if old.startswith("-") != new.startswith("-"):
        raise ValueError(f"alias {old!r} -> {new!r} mixes a command and an option")
    key = id(app)
    if key not in _DEPRECATED_ALIASES:
        _DEPRECATED_ALIASES[key] = {}
        _forget_with(app, _DEPRECATED_ALIASES)
    _DEPRECATED_ALIASES[key][old] = new


def deprecated_aliases(app: App) -> Mapping[str, str]:
    """The ``{old: new}`` deprecated spellings registered on ``app``."""
    return _DEPRECATED_ALIASES.get(id(app), {})
