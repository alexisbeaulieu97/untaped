"""Stability marks: which commands are experimental or deprecated, and what core does about it.

Every stability fact about a command lives here. The author marks the object
once (``@experimental`` or ``@deprecated(replacement=...)`` on a command
function, ``create_app(stability=...)`` on a group, ``stability=`` on a
:class:`~untaped.runtime.registry.PluginSpec` for a whole
plugin), and core supplies the panel in the parent's ``--help``, the last
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
  :func:`deprecated_alias`, a different mechanism with a successor;
- the removal version every deprecation names (``removed_in``), whose text
  :func:`removal_text` renders, and the one emitter of a deprecation on the
  sink, :func:`notify_deprecation`.
- deprecated behaviours (:func:`deprecated_behaviour`), which have no object
  to mark, so they carry a name and are listed on their spec.

Neither marker is, or subclasses, ``warnings.deprecated``: that one warns on
every use and, on a settings field, becomes pydantic field deprecation.
"""

from __future__ import annotations

import annotationlib
import ast
import functools
import inspect
import re
import types
import typing
import warnings
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cyclopts import App, Group
from cyclopts.command_spec import CommandSpec
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from untaped.sdk._block import OwnedBlock
from untaped.sdk._run import run_or_none
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.errors import ConfigError
from untaped.sdk.fields import SettingKey
from untaped.sdk.words import (
    EXPERIMENTAL_LINE,
    deprecated_ignored_message,
    deprecated_line,
    deprecated_message,
)

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult

_ROOT_NAME = "untaped"
_FUNCTION_ATTR = "__untaped_stability__"


class _Marker:
    """What both markers share: applied to a command function, they record themselves on it."""

    def __call__[F: Callable[..., Any]](self, target: F, /) -> F:
        """Mark a command function and return it unchanged (apply under ``@app.command``).

        On a descriptor (``classmethod``, ``staticmethod``, ``property``) the mark
        lands on its function, where the surface walker reads it.
        """
        setattr(descriptor_function(target), _FUNCTION_ATTR, self)
        return target


def descriptor_function(obj: object) -> object:
    """The function under a ``classmethod``, ``staticmethod``, ``property`` or bound method."""
    if isinstance(obj, property):
        return obj.fget if obj.fget is not None else obj
    if isinstance(obj, classmethod | staticmethod | types.MethodType):
        return obj.__func__
    return obj


@dataclass(frozen=True)
class Experimental(_Marker):
    """The ``experimental`` marker: may change or go away in a minor release."""


def check_removed_in(value: object) -> int | None:
    """``value`` when it is ``None`` or a major number (an ``int`` of at least 1, not a bool).

    Raises ``TypeError`` otherwise: a string or a minor would invite
    ``"12.1"``, which no release check should have to interpret.
    """
    if value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        return value
    raise TypeError(f"removed_in is the major number that removes it: removed_in=12, not {value!r}")


@dataclass(frozen=True, kw_only=True)
class Deprecated(_Marker):
    """The ``deprecated`` marker: still works, with a warning, until its removal version.

    ``removed_in`` is the major that removes it (``12``); ``None`` is undated
    and reads "a future major release". ``replacement`` is a command function
    or :class:`~cyclopts.App` (untaped derives its path), or text:
    ``"untaped github sync"`` or a setting key (shown in backticks) or prose
    (shown as is). In the 12.0 shape it is a command class or a
    ``key(Model).field``.
    """

    replacement: Callable[..., Any] | App | SettingKey | str | None = None
    removed_in: int | None = None

    def __post_init__(self) -> None:
        check_removed_in(self.removed_in)
        replacement = self.replacement
        if replacement is None or callable(replacement) or isinstance(replacement, SettingKey):
            return
        if not isinstance(replacement, str) or not replacement.strip():
            raise TypeError(
                "deprecated(replacement=...) takes a command function, an App or non-empty "
                f"text, not {replacement!r}"
            )


experimental = Experimental()
"""Mark a command, group or plugin experimental (``@experimental``, ``stability=``)."""


def deprecated(
    *,
    removed_in: int | None = None,
    replacement: Callable[..., Any] | App | str | None = None,
) -> Deprecated:
    """Mark a command, group, plugin or setting deprecated, naming its removal and replacement.

    ``removed_in`` is the major that removes it (``removed_in=12``; a plugin's
    own major for a plugin's command). Every deprecation names one: leaving
    it out is allowed until untaped 12 and reads "a future major release".
    Both are keyword-only, so a bare ``@deprecated`` fails at import. Use it
    as ``@deprecated(removed_in=12, replacement=set_command)`` under
    ``@app.command`` or as ``stability=deprecated(...)``.
    """
    return Deprecated(replacement=replacement, removed_in=removed_in)


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
        "call it: deprecated(removed_in=..., replacement=...)"
        if value is deprecated
        else "use untaped.sdk.experimental or untaped.sdk.deprecated(removed_in=..., "
        "replacement=...), not warnings.deprecated"
    )
    raise TypeError(f"{where}: stability must be experimental or deprecated(...); {hint}")


def function_mark(func: object) -> Stability | None:
    """The marker ``@experimental`` or ``@deprecated(...)`` put on a command function."""
    mark = getattr(func, _FUNCTION_ATTR, None)
    return mark if isinstance(mark, Experimental | Deprecated) else None


def class_mark(cls: type) -> Stability | None:
    """The marker put on ``cls`` itself; one a base class carries is not inherited."""
    mark = vars(cls).get(_FUNCTION_ATTR)
    return mark if isinstance(mark, Experimental | Deprecated) else None


# --- panels and the --deprecated flag ----------------------------------------


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
def command_selection() -> Iterator[None]:
    """The scope of one invocation's :func:`select_command`; the selection ends with it."""
    token = _SELECTED_COMMAND.set(None)
    try:
        yield
    finally:
        _SELECTED_COMMAND.reset(token)


def select_command(root: App, command: App, path: tuple[str, ...]) -> None:
    """Record ``command`` (at ``path``, ``("untaped", "awx", ...)``) as this invocation's."""
    _SELECTED_COMMAND.set((root, command, path))


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

A flag deprecated with no new spelling joins it beside its mark:
``dry_run: Annotated[bool, Parameter(group=DEPRECATED_OPTIONS), deprecated(removed_in=12,
replacement="--limit 1 --yes")] = False``. The root warns when any of its spellings is used.
"""


def marked_options(command: App) -> list[tuple[Any, Deprecated]]:
    """Each option of ``command``'s function marked ``deprecated(...)``, with its mark.

    Only a keyword-only parameter with its own spellings is an option: a mark on a positional
    or a flattened dataclass is not listed (``deprecated-option-panel`` reports it).
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


# --- marks on apps -----------------------------------------------------------


@dataclass(frozen=True)
class AppMark:
    """A mark on an app. ``source`` tells the author's own mark from one core applied."""

    stability: Stability
    source: Literal["own", "spec"]


# Keyed by ``id(app)``: cyclopts apps are unhashable. A finalizer drops the
# entry with the app, so a recycled id never inherits a stale mark.
_APP_MARKS: dict[int, AppMark] = {}
_DEPRECATED_ALIASES: dict[int, dict[str, Alias]] = {}


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
    """End ``app``'s help epilogue with ``line`` (once); ``own_only`` spares an app without one.

    A stability line written before the app was mounted (when its owner, and
    so its removal version's wording, was not known yet) is replaced, never
    kept beside the new one.
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
    app.help_epilogue = "\n\n".join([*paragraphs, line]) if paragraphs else line


def _is_stability_line(paragraph: str) -> bool:
    return paragraph == EXPERIMENTAL_LINE or paragraph.startswith(_DEPRECATED_LINE_START)


_DEPRECATED_LINE_START = deprecated_line().partition(" removed in ")[0] + " removed in "


# --- replacements ------------------------------------------------------------

COMMAND_TEXT = re.compile(r"^untaped(?: [a-z0-9][a-z0-9-]*)+$")
KEY_TEXT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


def children(app: App, *, resolve: bool) -> Iterator[tuple[str, App]]:
    """The mounted subcommands of ``app``, the way cyclopts' ``groups_from_app`` walks them.

    An unresolved lazy command is skipped unless ``resolve`` (reading it would
    import its plugin). A sub-app mounted under two names appears once.
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
    for name, sub in children(app, resolve=False):
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

    An object becomes `` `untaped <path>` ``; a text shaped like a command, a
    setting key or a flag (``--limit 1 --yes``) is backticked; prose stays as
    is. An object that is not found in ``tree`` (or no ``tree``) gives ``None``
    (an SDK callable's replacement is :func:`sdk_replacement_text`'s).
    """
    replacement = mark.replacement
    if replacement is None:
        return None
    if isinstance(replacement, str):
        shaped = (
            COMMAND_TEXT.match(replacement)
            or KEY_TEXT.match(replacement)
            or replacement.startswith("-")
        )
        return f"`{replacement}`" if shaped else replacement
    path = None if tree is None else replacement_path(tree, replacement, prefix=prefix)
    return None if path is None else f"`{' '.join(path)}`"


def sdk_replacement_text(mark: Deprecated) -> str | None:
    """The replacement of a deprecated SDK callable or parameter, as its warning shows it.

    An object is named by its backticked ``__qualname__`` (``Diagnostics.failure``),
    so a rename breaks the import rather than the text; text reads as
    :func:`replacement_text` shows it.
    """
    replacement = mark.replacement
    if replacement is None or isinstance(replacement, str):
        return replacement_text(mark, None)
    qualname = getattr(replacement, "__qualname__", None)
    return None if qualname is None else f"`{qualname}`"


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


# --- applying marks to a tree ------------------------------------------------


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


# --- the query ---------------------------------------------------------------


type MarkTarget = Literal["plugin", "group", "command", "option", "setting", "behaviour"]


@dataclass(frozen=True)
class Mark:
    """One mark: where it sits, what it marks, and what it says."""

    where: str
    """The command path (``awx test``), option (``awx test run --dry-run``), plugin name,
    setting key (``awx.test_parallel``) or behaviour (``untaped.plaintext-token``,
    ``<plugin>.<name>`` for a plugin's)."""
    target: MarkTarget
    stability: Stability
    replacement: str | None
    """The replacement as help shows it, or ``None``."""


def marks(root: App, result: CompositionResult, *, resolve: bool = False) -> list[Mark]:
    """Every mark in the composition: plugin specs, groups, commands, options, settings and
    behaviours.

    Takes the composition, not only the app, because a spec mark is not on a
    lazy app. A setting is recorded only for a mark of its own field; one
    inherited from its plugin is that plugin's record (ask
    :func:`setting_mark` for a setting's effective mark). ``resolve=True``
    imports every lazy plugin first (tests and generators only); the
    settings come from the sections the composition registered.
    """
    found: list[Mark] = []
    for plugin in result.plugins:
        spec = plugin.spec
        if spec.stability is not None:
            found.append(_record(spec.name, "plugin", spec.stability, root))
    plugins = frozenset(plugin.spec.name for plugin in result.plugins)
    _collect(root, root, (), plugins, found, resolve=resolve)
    found.extend(_setting_marks(root))
    found.extend(
        _record(f"{owner_name}.{behaviour.name}", "behaviour", behaviour.mark, root)
        for owner_name, behaviour in listed_behaviours(result)
    )
    return found


def listed_behaviours(result: CompositionResult) -> list[tuple[str, DeprecatedBehaviour]]:
    """``(owner, behaviour)`` for core's behaviours (owner ``untaped``), then each plugin's.

    Calls each spec's ``deprecations``, importing what it lists (checkers and
    generators only, never the CLI path).
    """
    listings = [(_CORE_OWNER, _core_behaviours)]
    listings.extend((plugin.spec.name, plugin.spec.deprecations) for plugin in result.plugins)
    found: list[tuple[str, DeprecatedBehaviour]] = []
    owners: dict[int, str] = {}
    for name, listing in listings:
        seen: set[str] = set()
        for item in listing():
            if not isinstance(item, DeprecatedBehaviour):
                raise ConfigError(
                    f"{name} deprecations lists {item!r}, not a deprecated_behaviour(...)"
                )
            if item.name in seen:
                raise ConfigError(f"{name} deprecations lists {item.name!r} twice")
            if id(item) in owners:
                raise ConfigError(
                    f"{name} deprecations lists {item.name!r}, which {owners[id(item)]} owns"
                )
            seen.add(item.name)
            owners[id(item)] = name
            found.append((name, item))
    return found


def _setting_marks(root: App) -> Iterator[Mark]:
    from untaped.runtime.settings import profile_section_models  # noqa: PLC0415

    for section, model in profile_section_models().items():
        for path, stability in field_marks(model).items():
            yield _record(f"{section}.{path}", "setting", stability, root)


def _record(where: str, target: MarkTarget, stability: Stability, root: App) -> Mark:
    text = replacement_text(stability, root) if isinstance(stability, Deprecated) else None
    return Mark(where=where, target=target, stability=stability, replacement=text)


def _collect(
    app: App,
    root: App,
    path: tuple[str, ...],
    plugins: frozenset[str],
    found: list[Mark],
    *,
    resolve: bool,
) -> None:
    for name, sub in children(app, resolve=resolve):
        here = (*path, name)
        mark = mark_of(sub)
        entry = app_mark(sub)
        if mark is not None and not (entry is not None and entry.source == "spec"):
            target: MarkTarget = (
                "plugin"
                if not path and name in plugins
                else ("group" if any(True for _ in children(sub, resolve=False)) else "command")
            )
            found.append(_record(" ".join(here), target, mark, root))
        found.extend(
            _record(f"{' '.join(here)} {option_name(argument)}", "option", option, root)
            for argument, option in marked_options(sub)
        )
        _collect(sub, root, here, plugins, found, resolve=resolve)


# --- marks on settings -------------------------------------------------------


def _own_marks(field: FieldInfo) -> list[Stability]:
    """The marks in the outermost ``Annotated`` of a field (``FieldInfo.metadata``)."""
    return [item for item in field.metadata if isinstance(item, Experimental | Deprecated)]


def _mark_inside_type(annotation: object) -> bool:
    """Whether a mark sits inside the type itself, where pydantic drops it.

    ``Annotated[int, experimental] | None`` and ``list[Annotated[int, ...]]``
    keep their marks in ``FieldInfo.annotation``; ``Annotated[int | None,
    experimental]`` moves its mark to ``FieldInfo.metadata``.
    """
    return any(
        isinstance(arg, Experimental | Deprecated) or _mark_inside_type(arg)
        for arg in typing.get_args(annotation)
    )


def _section_fields(
    model: type[BaseModel], prefix: str = ""
) -> Iterator[tuple[str, FieldInfo, bool]]:
    """``(dotted path, field, is a model)`` for every field, recursing into nested models."""
    for name, field in model.model_fields.items():
        nested = unwrap_optional(field.annotation)
        is_model = isinstance(nested, type) and issubclass(nested, BaseModel)
        yield f"{prefix}{name}", field, is_model
        if is_model:
            yield from _section_fields(nested, f"{prefix}{name}.")


def field_descriptions(model: type[BaseModel]) -> dict[str, str]:
    """The ``description`` of each described leaf field of a section ``model``, by dotted path."""
    return {
        path: field.description
        for path, field, is_model in _section_fields(model)
        if field.description and not is_model
    }


def field_marks(model: type[BaseModel]) -> dict[str, Stability]:
    """The marks on the leaf fields of a section ``model``, by dotted path.

    Only a mark in the outermost ``Annotated`` of a leaf counts; a misplaced
    one is reported by :func:`mark_errors` and ignored here.
    """
    found: dict[str, Stability] = {}
    for path, field, is_model in _section_fields(model):
        marks_here = _own_marks(field)
        if len(marks_here) == 1 and not is_model:
            found[path] = marks_here[0]
    return found


def pydantic_deprecated_fields(model: type[BaseModel]) -> list[str]:
    """The dotted paths of the fields of ``model`` that use pydantic's own ``deprecated=``."""
    return [path for path, field, _ in _section_fields(model) if field.deprecated]


def mark_errors(model: type[BaseModel], *, state: bool = False) -> list[str]:
    """One sentence per mark on ``model`` that would not take effect; ``[]`` when none.

    A mark must sit in the outermost ``Annotated`` of a leaf field of a profile
    model (``Annotated[int | None, experimental]``), never inside a union or
    generic argument, on a model-typed field, on a state model's field
    (``state=True``). A field with pydantic's own ``deprecated=`` is a style
    problem for ``check_conventions`` (:func:`pydantic_deprecated_fields`), not
    one that stops a provider loading.
    """
    errors: list[str] = []
    for path, field, is_model in _section_fields(model):
        marks_here = _own_marks(field)
        inside = _mark_inside_type(field.annotation)
        if state:
            if marks_here or inside:
                errors.append(f"setting {path!r} is state: state fields take no stability marks")
            continue
        if inside:
            errors.append(
                f"the mark on setting {path!r} sits inside its type, where pydantic drops it; "
                "put it in the outermost Annotated, as Annotated[int | None, experimental]"
            )
        if marks_here and is_model:
            errors.append(f"setting {path!r} is a model; mark its fields instead")
        if len(marks_here) > 1:
            errors.append(f"setting {path!r} has more than one stability mark")
        errors.extend(
            f"the replacement of the mark on setting {path!r} must be text"
            for mark in marks_here
            if isinstance(mark, Deprecated)
            and mark.replacement is not None
            and not isinstance(mark.replacement, str)
        )
    return errors


type SettingStability = Literal["stable", "experimental", "deprecated"]


def setting_mark(
    key: str,
    *,
    sections: Mapping[str, type[BaseModel]],
    section_stability: Mapping[str, Stability | None],
) -> Stability | None:
    """The effective mark of setting ``key`` (``section.field``), or ``None`` when stable.

    The field's own mark wins; otherwise the setting inherits the mark of the
    plugin that owns its section. Pure: ``section_stability`` is what
    :func:`untaped.runtime.settings.section_stabilities` returns.
    """
    section, _, rest = key.partition(".")
    model = sections.get(section)
    own = None if model is None else field_marks(model).get(rest)
    return own if own is not None else section_stability.get(section)


def stability_name(mark: Stability | None) -> SettingStability:
    """``mark`` as the word ``config list`` and ``config get`` show."""
    if mark is None:
        return "stable"
    return "experimental" if isinstance(mark, Experimental) else "deprecated"


# --- renamed commands and options -------------------------------------------


@dataclass(frozen=True)
class Alias:
    """A deprecated spelling's target and the major that removes the spelling."""

    new: str
    removed_in: int | None = None


def deprecated_alias(app: App, old: str, new: str, *, removed_in: int | None = None) -> None:
    """Keep ``old`` working as a hidden, deprecated spelling of ``new`` on ``app``.

    For a renamed command or group, ``app`` is its parent and the names are
    command names (``deprecated_alias(jira_app, "me", "whoami", removed_in=12)``).
    For a renamed option or short flag, ``app`` is the command itself and the
    names are flags (``deprecated_alias(logs_app, "-f", "--follow", removed_in=12)``).
    The root shell rewrites the old token to the new one before dispatch and
    prints a warning naming both on stderr; the old spelling never appears in
    ``--help``. Aliases apply to invocations through the ``untaped`` root
    (test them with ``build_root_app``). ``removed_in`` is the major that
    removes the old spelling; leaving it out is allowed until untaped 12 and
    reads "a future major release".
    """
    check_removed_in(removed_in)
    if old.startswith("-") != new.startswith("-"):
        raise ValueError(f"alias {old!r} -> {new!r} mixes a command and an option")
    key = id(app)
    if key not in _DEPRECATED_ALIASES:
        _DEPRECATED_ALIASES[key] = {}
        _forget_with(app, _DEPRECATED_ALIASES)
    _DEPRECATED_ALIASES[key][old] = Alias(new, removed_in)


def deprecated_aliases(app: App) -> Mapping[str, Alias]:
    """The deprecated spellings registered on ``app``, old spelling → :class:`Alias`."""
    return _DEPRECATED_ALIASES.get(id(app), {})


# --- removal versions and the deprecation emitter ------------------------------

# Plugin name → its distribution, bound by the composition (``bind_owners``):
# a plugin's deprecations name whose major they mean, core's own name none.
_OWNERS: dict[str, str] = {}


def bind_owners(owners: Mapping[str, str]) -> None:
    """Record each composed plugin's distribution (plugin name → distribution), anew."""
    _OWNERS.clear()
    _OWNERS.update(owners)


def owner(name: str) -> str | None:
    """The distribution of composed plugin ``name`` (a command's first word, a setting's section).

    ``None`` for core's own commands and sections, and outside a composition.
    """
    return _OWNERS.get(name)


def path_owner(path: tuple[str, ...]) -> str | None:
    """The owner of the command at ``path`` (``("untaped", "awx", "put")``)."""
    return owner(path[1]) if len(path) > 1 else None


def removal_text(removed_in: int | None, owner: str | None) -> str | None:
    """``12.0`` for core's own, ``untaped-awx 12.0`` for a plugin's, ``None`` when undated.

    A plugin's major is its own, so its text names its distribution, whoever
    maintains it.
    """
    if removed_in is None:
        return None
    return f"{removed_in}.0" if owner is None else f"{owner} {removed_in}.0"


class UntapedDeprecationWarning(DeprecationWarning):
    """A deprecation of a Python API, carrying what the ``untaped.deprecation`` record carries.

    ``id`` is the deprecated thing's surface identity, ``removed_in`` its
    removal major (``None`` undated), ``distribution`` its owner (``None``
    for core's own) and ``replacement`` what to use instead.
    """

    def __init__(
        self,
        message: str,
        *,
        id: str,
        removed_in: int | None = None,
        distribution: str | None = None,
        replacement: str | None = None,
    ) -> None:
        super().__init__(message)
        self.id = id
        self.removed_in = removed_in
        self.distribution = distribution
        self.replacement = replacement


_NOTIFIED: set[str] = set()


def first_notice(key: str) -> bool:
    """Whether ``key`` has not been warned about in this process yet (and remember it)."""
    if key in _NOTIFIED:
        return False
    _NOTIFIED.add(key)
    return True


def forget_notices() -> None:
    """Forget which deprecations were warned about (tests, embedding)."""
    _NOTIFIED.clear()


def notify_deprecation(
    id: str,
    subject: str,
    *,
    removed_in: int | None,
    owner: str | None = None,
    replacement: str | None = None,
    kept: str | None = None,
    key: str | None = None,
    hint: str | None = None,
) -> None:
    """Warn once that ``subject`` is deprecated, as an ``untaped.deprecation`` record.

    The one emitter of a deprecation on the sink: ``id`` is the deprecated
    thing's surface identity (``cli untaped awx put``), ``subject`` how the
    sentence names it, ``replacement`` the replacement as shown (backticks
    included). ``kept`` says the old spelling was ignored because ``kept``
    is also set. Once per process per ``key`` (else per ``id``), so callers
    warning about one thing in different words share it. ``hint`` is the
    follow-up, its own line and its own field under JSON.
    """
    if not first_notice(id if key is None else key):
        return
    from untaped.runtime.sink import Diagnostics  # noqa: PLC0415
    from untaped.sdk.diagnostic import DeprecationNotice  # noqa: PLC0415 - off the CLI path

    removal = removal_text(removed_in, owner)
    if kept is None:
        message = deprecated_message(subject, replacement, removal)
    else:
        message = deprecated_ignored_message(subject, kept, replacement or kept, removal)
    Diagnostics().emit(
        DeprecationNotice(
            message=message,
            hint=hint,
            id=id,
            removed_in=removed_in,
            distribution=owner,
            replacement=None if replacement is None else replacement.replace("`", ""),
        )
    )


# --- deprecated SDK callables and parameters ----------------------------------

_PLUGIN_PACKAGE = "untaped_"
_PARAMETERS_ATTR = "__untaped_deprecated_parameters__"


def callable_owner(obj: object) -> str | None:
    """The distribution whose major a deprecated SDK callable names; ``None`` for core's.

    Read from the ``untaped_<name>`` package of its ``__module__``, the rule
    every plugin's package follows: the composed plugin's distribution, else
    ``untaped-<name>`` (every plugin's distribution is named after it). No
    package metadata is read.
    """
    package = str(getattr(obj, "__module__", "") or "").partition(".")[0]
    if not package.startswith(_PLUGIN_PACKAGE):
        return None
    name = package.removeprefix(_PLUGIN_PACKAGE).replace("_", "-")
    return owner(name) or f"untaped-{name}"


def deprecation_sentence(obj: object, mark: Deprecated, *, subject: str | None = None) -> str:
    """The deprecation sentence of SDK callable ``obj``: what its ``warnings.deprecated`` says.

    ``<qualname> is deprecated and will be removed in <version>[; use <replacement>]``;
    ``subject`` replaces the qualname (a parameter's ``echo(err=)``).
    """
    name = subject if subject is not None else str(getattr(obj, "__qualname__", obj))
    removal = removal_text(mark.removed_in, callable_owner(obj))
    return deprecated_message(name, sdk_replacement_text(mark), removal)


def deprecated_parameters[F: Callable[..., Any]](function: F, /) -> F:
    """Warn whenever a parameter marked ``deprecated(...)`` in its annotation is passed.

    ``def emit(rows, *, kind: Annotated[str | None, deprecated(removed_in=12,
    replacement="kind= on the Record class")] = None)`` under
    ``@deprecated_parameters`` raises an :class:`UntapedDeprecationWarning`
    each time ``kind`` is passed, its default value included, since the major
    that removes it makes any spelling of it a ``TypeError``. The mark on the
    parameter is the declaration (its surface line shows it); the decorator
    is what warns. Signatures see through it, and an ``async def`` stays one.
    """
    if isinstance(function, classmethod | staticmethod | property):
        raise TypeError(
            f"put @deprecated_parameters under @{type(function).__name__}, on the function"
        )
    marked: dict[str, Deprecated] | None = None
    signature: inspect.Signature | None = None
    by_keyword_only = False

    def warn_for(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        nonlocal marked, signature, by_keyword_only
        if marked is None:
            signature = inspect.signature(
                function, annotation_format=annotationlib.Format.FORWARDREF
            )
            marked = parameter_marks(function)
            keyword_only = inspect.Parameter.KEYWORD_ONLY
            by_keyword_only = all(signature.parameters[n].kind is keyword_only for n in marked)
        if not marked or (by_keyword_only and not kwargs.keys() & marked.keys()):
            return
        try:
            bound = typing.cast(inspect.Signature, signature).bind_partial(*args, **kwargs)
        except TypeError:
            return  # the call itself raises, in Python's words
        for name in marked.keys() & bound.arguments.keys():
            mark = marked[name]
            subject = f"{function.__qualname__}({name}=)"
            shown = sdk_replacement_text(mark)
            warning = UntapedDeprecationWarning(
                deprecation_sentence(function, mark, subject=subject),
                id=f"sdk {subject}",
                removed_in=mark.removed_in,
                distribution=callable_owner(function),
                replacement=None if shown is None else shown.replace("`", ""),
            )
            warnings.warn(warning, stacklevel=3)  # untaped: allow warnings-warn

    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def awaiting(*args: Any, **kwargs: Any) -> Any:
            warn_for(args, kwargs)
            return await function(*args, **kwargs)

        wrapper: Callable[..., Any] = awaiting
    else:

        @functools.wraps(function)
        def calling(*args: Any, **kwargs: Any) -> Any:
            warn_for(args, kwargs)
            return function(*args, **kwargs)

        wrapper = calling
    setattr(wrapper, _PARAMETERS_ATTR, True)
    return typing.cast("F", wrapper)


def warns_parameters(function: object) -> bool:
    """Whether ``function`` (or a descriptor's) is under :func:`deprecated_parameters`."""
    return bool(getattr(descriptor_function(function), _PARAMETERS_ATTR, False))


def parameter_marks(function: Callable[..., Any]) -> dict[str, Deprecated]:
    """Each parameter of ``function`` whose annotation carries a ``deprecated(...)`` mark.

    Reads the annotations without evaluating them (``annotationlib``,
    ``FORWARDREF``) and evaluates only a string annotation's ``Annotated``
    metadata, so a hint imported under ``TYPE_CHECKING`` never raises. The
    ``Annotated`` may sit inside a union (``Annotated[bool, deprecated(...)] | None``).
    """
    return read_parameter_marks(function)[0]


def read_parameter_marks(
    function: Callable[..., Any],
) -> tuple[dict[str, Deprecated], dict[str, str]]:
    """:func:`parameter_marks`, and each parameter whose mark could not be evaluated, why."""
    target = inspect.unwrap(function)
    try:
        found = annotationlib.get_annotations(target, format=annotationlib.Format.FORWARDREF)
    except TypeError:
        return {}, {}
    namespace = getattr(target, "__globals__", {})
    marks: dict[str, Deprecated] = {}
    failures: dict[str, str] = {}
    for name, annotation in found.items():
        if name == "return":
            continue
        problems: list[str] = []
        mark = next(
            (
                item
                for item in _metadata(annotation, namespace, problems)
                if isinstance(item, Deprecated)
            ),
            None,
        )
        # Read as FORWARDREF, a name the module lacks comes back unresolved, not as an error.
        unresolved: object = None if mark is None else mark.replacement
        if isinstance(unresolved, annotationlib.ForwardRef):
            problems.append(f"replacement={unresolved.__forward_arg__} is not defined")
            mark = None
        if mark is not None:
            marks[name] = mark
        elif problems:
            failures[name] = problems[0]
    return marks, failures


def annotation_metadata(annotation: object) -> list[object]:
    """The ``Annotated`` metadata of a resolved type, a union member's too."""
    return _metadata(annotation, {}, [])


def _metadata(
    annotation: object, namespace: Mapping[str, Any], problems: list[str]
) -> list[object]:
    """The ``Annotated`` metadata in ``annotation`` (a type or its source text), a union's too.

    A string's ``deprecated(...)`` that fails to evaluate adds its error to ``problems``.
    """
    if isinstance(annotation, annotationlib.ForwardRef):
        annotation = annotation.__forward_arg__
    if not isinstance(annotation, str):
        metadata = list(getattr(annotation, "__metadata__", ()))
        if not metadata and isinstance(annotation, types.UnionType):
            for argument in typing.get_args(annotation):
                metadata.extend(_metadata(argument, namespace, problems))
        return metadata
    return _text_metadata(annotation, namespace, problems) if "deprecated" in annotation else []


def _text_metadata(
    annotation: str, namespace: Mapping[str, Any], problems: list[str]
) -> list[object]:
    """The evaluated ``Annotated`` metadata of an annotation's source text."""
    try:
        tree = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return []
    found: list[object] = []
    for node in _union_members(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Tuple)):
            continue
        if _last_name(node.value) != "Annotated":
            continue
        for element in node.slice.elts[1:]:
            try:
                found.append(
                    eval(compile(ast.Expression(element), "<annotation>", "eval"), dict(namespace))
                )
            except Exception as exc:  # reported by the checks, never at a call
                if isinstance(element, ast.Call) and _last_name(element.func) == "deprecated":
                    problems.append(f"{ast.unparse(element)}: {type(exc).__name__}: {exc}")
    return found


def _union_members(node: ast.expr) -> Iterator[ast.expr]:
    """``node``, or each member of the union it spells (``X | None``, ``Optional[X]``)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _union_members(node.left)
        yield from _union_members(node.right)
    elif isinstance(node, ast.Subscript) and _last_name(node.value) in ("Optional", "Union"):
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for member in members:
            yield from _union_members(member)
    else:
        yield node


def _last_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


# --- deprecated behaviours ---------------------------------------------------

_BEHAVIOUR_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_CORE_OWNER = _ROOT_NAME


class _Default:
    """The behaviour's own replacement, where ``replacement=None`` would mean none."""

    def __repr__(self) -> str:
        return "<the behaviour's replacement>"


_OWN = _Default()


class DeprecatedBehaviour(OwnedBlock):
    """A deprecated behaviour: a promise with no object to mark, so it carries a name.

    A plugin class holding it names it: ``plaintext_token =
    DeprecatedBehaviour(removed_in=13, replacement="untaped auth migrate")`` is
    the plugin's behaviour ``plaintext-token``. The 11.x spelling is
    :func:`deprecated_behaviour`, listed on the spec of the plugin that owns it
    (``PluginSpec(deprecations=lambda: (ROW_KIND,))``). The surface writes
    ``behaviour <plugin>.<name>`` for a listed one, so the release check holds
    it to its date; a held one reaches the surface once it is read from the
    declaration, with the mount. A site never types the words: it names the subject and calls
    :meth:`sentence`, :meth:`notify` or :meth:`warn`.
    """

    block_kind = "deprecated-behaviour"
    name_style = "kebab"

    mark: Deprecated

    def __init__(
        self,
        name: str | None = None,
        mark: Deprecated | None = None,
        *,
        removed_in: int | None = None,
        replacement: str | None = None,
    ) -> None:
        super().__init__()
        if (name is None) != (mark is None):
            raise TypeError(
                "a deprecated behaviour takes its name and mark together, or removed_in= and "
                "replacement= when a plugin class holds it"
            )
        if name is not None and (problem := self.name_problem(name)) is not None:
            raise TypeError(problem)
        if mark is None:
            mark = Deprecated(replacement=replacement, removed_in=removed_in)
        elif removed_in is not None or replacement is not None:
            raise TypeError("a deprecated behaviour's mark already says removed_in and replacement")
        if not isinstance(mark, Deprecated) or callable(mark.replacement):
            raise TypeError("a deprecated behaviour's replacement is text or None")
        if mark.removed_in is None:
            raise TypeError("a deprecated behaviour names the major that removes it: removed_in=12")
        self._spelled_name = name
        self.mark = mark

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if isinstance(name, str) and _BEHAVIOUR_NAME.fullmatch(name):
            return None
        return f"a deprecated behaviour's name is kebab-case (plaintext-token), not {name!r}"

    @property
    def spelled(self) -> bool:
        return self._spelled_name is not None

    @property
    def name(self) -> str:
        return self._spelled_name if self._spelled_name is not None else super().name

    @property
    def plugin(self) -> str:
        """The plugin it belongs to: the one holding it, or listing it (``untaped`` for core's)."""
        if self._spelled_name is None:
            return super().plugin
        return behaviour_owner(self) or _CORE_OWNER

    @property
    def where(self) -> str:
        return self._spelled_name if self._spelled_name is not None else super().where

    def __repr__(self) -> str:
        return f"DeprecatedBehaviour({self.where!r}, removed_in={self.mark.removed_in})"

    @property
    def replacement(self) -> str | None:
        """The replacement as warnings show it (a command or key backticked), or ``None``."""
        return replacement_text(self.mark, None)

    @property
    def id(self) -> str:
        """Its surface identity: ``behaviour <plugin>.<name>``, with ``untaped`` for core's.

        The plugin is the one holding or listing it (:attr:`plugin`).
        """
        return f"behaviour {self.plugin}.{self.name}"

    def _shown(self, replacement: str | None | _Default) -> str | None:
        if isinstance(replacement, _Default):
            return self.replacement
        if replacement is None:
            return None
        return replacement_text(Deprecated(replacement=replacement), None)

    def sentence(self, subject: str, *, replacement: str | None | _Default = _OWN) -> str:
        """``<subject> is deprecated and will be removed in <version>[; use <replacement>]``.

        ``subject`` names the support that goes away ("support for a plain-text
        awx.token in config.yml"). ``replacement`` overrides the behaviour's own
        for one site (text, or ``None`` for none: a row that shows its fix already).
        """
        removal = removal_text(self.mark.removed_in, _distribution(self))
        return deprecated_message(subject, self._shown(replacement), removal)

    def notify(
        self,
        subject: str,
        *,
        key: str,
        hint: str | None = None,
        replacement: str | None | _Default = _OWN,
    ) -> None:
        """Warn once per ``key`` on the sink, as an ``untaped.deprecation`` record.

        ``key`` is this behaviour's own (two sites passing one key share one
        warning); ``hint`` is the follow-up (:func:`~untaped.sdk.words.run_hint`),
        its own line and its own field under JSON.
        """
        notify_deprecation(
            self.id,
            subject,
            removed_in=self.mark.removed_in,
            owner=_distribution(self),
            replacement=self._shown(replacement),
            key=f"{self.id} {key}",
            hint=hint,
        )

    def warn(
        self, subject: str, *, stacklevel: int = 2, replacement: str | None | _Default = _OWN
    ) -> None:
        """Warn with an :class:`UntapedDeprecationWarning`, for a behaviour of a Python API.

        ``stacklevel`` counts from the caller of ``warn``, as ``warnings.warn``'s does.
        """
        shown = self._shown(replacement)
        warning = UntapedDeprecationWarning(
            self.sentence(subject, replacement=replacement),
            id=self.id,
            removed_in=self.mark.removed_in,
            distribution=_distribution(self),
            replacement=None if shown is None else shown.replace("`", ""),
        )
        warnings.warn(warning, stacklevel=stacklevel + 1)  # untaped: allow warnings-warn


def deprecated_behaviour(
    name: str, *, removed_in: int, replacement: str | None = None
) -> DeprecatedBehaviour:
    """Declare a deprecated behaviour: ``name`` (kebab-case), its removal major and replacement.

    ``PLAINTEXT_TOKEN = deprecated_behaviour("plaintext-token", removed_in=12,
    replacement="untaped auth migrate")``. List it on the owning spec's
    ``deprecations``; a plugin's ``removed_in`` is its own major.
    """
    return DeprecatedBehaviour(name, Deprecated(replacement=replacement, removed_in=removed_in))


def _core_behaviours() -> Sequence[DeprecatedBehaviour]:
    """Core's own deprecated behaviours, the SDK's: each goes in the 12.0 removal sweep.

    Never ``Core`` attributes (core move ruling 3): they are listed here,
    beside the mechanic that reads them, until the sweep removes them, and
    this listing leaves with them in that sweep.
    """
    from untaped.sdk.health.marks import UNMARKED_SERVICE  # noqa: PLC0415 - off the CLI path
    from untaped.sdk.network.auth import PLAINTEXT_TOKEN  # noqa: PLC0415
    from untaped.sdk.network.http import CONNECTED_BY_NAME  # noqa: PLC0415

    return (CONNECTED_BY_NAME, PLAINTEXT_TOKEN, UNMARKED_SERVICE)


# Plugin name → the ``deprecations`` callable of its spec, bound by the
# composition and called only when a behaviour first asks who lists it, so a
# spec module never imports its behaviours to compose. Core's are the rest.
_LISTINGS: dict[str, Callable[[], Sequence[DeprecatedBehaviour]]] = {}
_BEHAVIOUR_OWNERS: dict[int, tuple[DeprecatedBehaviour, str | None]] = {}


def bind_behaviours(listings: Mapping[str, Callable[[], Sequence[DeprecatedBehaviour]]]) -> None:
    """Record each composed plugin's ``deprecations`` (plugin name → its callable), anew."""
    _LISTINGS.clear()
    _LISTINGS.update(listings)
    _BEHAVIOUR_OWNERS.clear()


def behaviour_owner(behaviour: DeprecatedBehaviour) -> str | None:
    """The plugin whose spec lists ``behaviour``, or ``None`` for core's.

    Bound by the composition's listings: core's own come first (so no plugin
    can claim one), then each plugin's. One nobody lists, or used outside a
    composition, reads as core's; ``tests/repo`` keeps this workspace's
    behaviours listed.
    """
    key = id(behaviour)
    if key not in _BEHAVIOUR_OWNERS:
        found: str | None = None
        if not any(item is behaviour for item in _core_behaviours()):
            listers = (n for n, items in _listed() if any(item is behaviour for item in items))
            found = next(listers, None)
        _BEHAVIOUR_OWNERS[key] = (behaviour, found)
    return _BEHAVIOUR_OWNERS[key][1]


def _distribution(behaviour: DeprecatedBehaviour) -> str | None:
    """The distribution whose major ``behaviour`` names; ``None`` for core's."""
    plugin = behaviour.plugin
    return None if plugin == _CORE_OWNER else owner(plugin)


def _listed() -> Iterator[tuple[str, list[DeprecatedBehaviour]]]:
    """Each composed plugin's listed behaviours; a listing that fails to load lists none.

    Asked while warning, so a broken plugin listing never turns a warning into a crash.
    """
    for name, listing in _LISTINGS.items():
        try:
            items = list(listing())
        except Exception:
            continue
        yield name, [item for item in items if isinstance(item, DeprecatedBehaviour)]
