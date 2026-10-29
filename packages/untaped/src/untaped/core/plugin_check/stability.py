"""Stability rules: experimental and deprecated marks are placed and worded by core.

Checks every command of a subtree against ``docs/plugins.md``:

- ``hand-typed-mark`` — an ``Experimental:`` or ``Deprecated:`` line in an app
  help, a command docstring, a parameter's help (a hidden one's too) or a
  settings field's description (core writes the line from the mark);
- ``wrong-deprecated`` — a settings field carries pydantic's ``deprecated=``,
  where untaped's ``deprecated(...)`` alone belongs; an SDK callable is the
  other way round and carries both (below);
- ``nested-mark`` — a mark (a deprecated option's too) under a mark that makes
  it redundant or contradictory: experimental under experimental, or anything under
  deprecated (deprecated under experimental is allowed); a settings field
  sits under its plugin;
- ``bad-replacement`` — a ``deprecated`` replacement that is not mounted, is
  itself deprecated, or is text naming a command that does not resolve, a
  command of the plugin itself (pass the object), or a setting that does
  not exist (a setting's replacement is always text); composition already
  refuses a plugin class whose replacement class is not its command or is
  deprecated, or whose text names its own command.

A deprecated behaviour (``<plugin>.<name>``) is checked for ``nested-mark``
(under its deprecated plugin) and ``bad-replacement`` (its text), the
checked plugin's or, with no plugin, core's.

Every deprecation is a promise with a date, so:

- ``undated-deprecation`` — a mark of the checked plugin with no
  ``removed_in``: a command, group, alias, setting, renamed key or cache
  rename (:func:`undated_violations`, read from its surface lines), or an
  export of its ``api`` or ``testing`` module (below);
- ``untyped-deprecation``, ``deprecation-text`` and ``unwarned-parameter`` —
  an exported SDK callable or class marked for one audience only, whose
  ``warnings.deprecated`` literal is not its mark's sentence, or with a
  marked parameter and no ``@deprecated_parameters``
  (:func:`deprecation_text_violations`, which the repo's
  ``test_sdk_deprecations`` runs over core's SDK modules).

A group named after a stability panel is ``reserved-panel``, one of the help
tree's rules (:mod:`untaped.core.plugin_check.help_tree`).

Lines are ``<command path or setting key>::<rule>::<detail>``; they carry no
source line, so no inline marker can suppress them.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Iterator
from types import ModuleType
from typing import TYPE_CHECKING, Any

from cyclopts import App

from untaped.core.surface._sdk import foreign
from untaped.runtime.registry import CompositionResult, PluginCandidate
from untaped.runtime.root_options import resolve_command
from untaped.runtime.settings import get_settings_model, profile_section_models
from untaped.sdk._schema import walk_settings
from untaped.sdk.marks import (
    COMMAND_TEXT,
    KEY_TEXT,
    Deprecated,
    Experimental,
    Mark,
    Stability,
    children,
    class_mark,
    deprecation_sentence,
    descriptor_function,
    field_descriptions,
    function_mark,
    mark_of,
    marks,
    pydantic_deprecated_fields,
    read_parameter_marks,
    replacement_path,
    warns_parameters,
)

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

_HAND_TYPED = re.compile(r"\b(?:Experimental|Deprecated):")


def _kind(stability: Stability) -> str:
    return "experimental" if isinstance(stability, Experimental) else "deprecated"


def _walk(app: App, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], App]]:
    for name, sub in children(app, resolve=False):
        yield (*path, name), sub
        yield from _walk(sub, (*path, name))


def stability_violations(
    root: App,
    result: CompositionResult,
    names: Iterable[str],
    *,
    spec: ComposedPlugin | None = None,
    sections: Iterable[str] = (),
) -> list[str]:
    """``["<command path>::<rule>::<detail>", ...]`` for the subtrees ``root[name]``.

    ``spec`` is the checked plugin's: its settings section is checked too, as
    are the ``sections`` named (core's).
    The marks come from :func:`untaped.sdk.marks.marks` over the whole
    composition: the checked plugin's subtree is resolved by ``root[name]``;
    a lazy sibling is never imported (its spec mark is still seen).
    """
    wanted = frozenset(names)
    settings = frozenset(sections) | ({spec.name} if spec is not None else frozenset())
    found: list[str] = []
    for top in sorted(wanted):
        subtree = root[top]
        for path, app in [((top,), subtree), *_walk(subtree, (top,))]:
            where = " ".join(path)
            found.extend(f"{where}::hand-typed-mark::{detail}" for detail in _hand_typed(app))
    every = marks(root, result)
    found.extend(_setting_violations(root, result, settings, every))
    found.extend(_behaviour_violations(root, result, spec, every))
    # composition spells a plugin class's own replacements and refuses text naming them
    own = None if spec is None or _is_class(result, spec.name) else spec.name
    for mark in (mark for mark in every if mark.target not in ("setting", "behaviour")):
        if mark.where.split()[0] in wanted:
            found.extend(f"{mark.where}::nested-mark::{d}" for d in _nested(mark, every))
            if isinstance(mark.stability, Deprecated):
                found.extend(
                    f"{mark.where}::bad-replacement::{d}"
                    for d in _bad_replacement(root, mark.stability, own)
                )
    return sorted(found)


def _is_class(result: CompositionResult, name: str) -> bool:
    """Whether plugin ``name`` is a plugin class, which its declaration holds."""
    return any(plugin.name == name for plugin in result.mount.declaration.plugins)


def _setting_violations(
    root: App, result: CompositionResult, sections: frozenset[str], every: list[Mark]
) -> Iterator[str]:
    """The rules for the descriptions and marks of the settings of ``sections``."""
    models = profile_section_models()
    for section in sorted(sections & models.keys()):
        for path, text in field_descriptions(models[section]).items():
            if _HAND_TYPED.search(text):
                yield f"{section}.{path}::hand-typed-mark::description"
        for path in pydantic_deprecated_fields(models[section]):
            yield (
                f"{section}.{path}::wrong-deprecated::"
                "uses pydantic's deprecated=; use untaped's deprecated(...)"
            )
    plugin = {plugin.spec.name: plugin.spec.stability for plugin in result.plugins}
    for mark in every:
        section = mark.where.partition(".")[0]
        if mark.target != "setting" or section not in sections:
            continue
        above = plugin.get(section)
        if isinstance(above, Deprecated):
            yield f"{mark.where}::nested-mark::{_kind(mark.stability)} under deprecated {section}"
        elif isinstance(above, Experimental) and isinstance(mark.stability, Experimental):
            yield f"{mark.where}::nested-mark::experimental under experimental {section}"
        if isinstance(mark.stability, Deprecated) and isinstance(mark.stability.replacement, str):
            yield from (
                f"{mark.where}::bad-replacement::{detail}"
                for detail in _bad_text(root, mark.stability.replacement, None)
            )


def _behaviour_violations(
    root: App, result: CompositionResult, spec: ComposedPlugin | None, every: list[Mark]
) -> Iterator[str]:
    """The rules for the checked plugin's deprecated behaviours (core's when ``spec`` is None)."""
    owner = "untaped" if spec is None else spec.name
    plugin = {plugin.spec.name: plugin.spec.stability for plugin in result.plugins}
    for mark in every:
        if mark.target != "behaviour" or mark.where.partition(".")[0] != owner:
            continue
        if isinstance(plugin.get(owner), Deprecated):
            yield f"{mark.where}::nested-mark::deprecated under deprecated {owner}"
        if isinstance(mark.stability, Deprecated) and isinstance(mark.stability.replacement, str):
            # A behaviour names its replacement as text only, so its own commands are fine.
            yield from (
                f"{mark.where}::bad-replacement::{detail}"
                for detail in _bad_text(root, mark.stability.replacement, None)
            )


def _nested(mark: Mark, every: list[Mark]) -> Iterator[str]:
    """What makes ``mark`` redundant or contradictory: an enclosing mark of another mark."""
    own = tuple(mark.where.split())
    for above in every:
        outer = tuple(above.where.split())
        if len(outer) >= len(own) or own[: len(outer)] != outer:
            continue
        if isinstance(above.stability, Deprecated):
            yield f"{_kind(mark.stability)} under deprecated {above.where}"
        elif isinstance(mark.stability, Experimental):
            yield f"experimental under experimental {above.where}"


def _hand_typed(app: App) -> Iterator[str]:
    if app.default_command is None:
        if isinstance(app.help, str) and _HAND_TYPED.search(app.help):
            yield "help"
        return
    # A command's help is its docstring, so only the docstring is checked.
    docstring = inspect.getdoc(app.default_command)
    if docstring and _HAND_TYPED.search(docstring):
        yield "docstring"
    for argument in app.assemble_argument_collection(parse_docstring=True):
        if argument.parse:
            text = argument.parameter.help
            if text and _HAND_TYPED.search(text):
                yield f"{argument.names[0] if argument.names else argument.field_info.name} help"


def _bad_replacement(root: App, mark: Deprecated, plugin: str | None) -> Iterator[str]:
    replacement = mark.replacement
    if replacement is None:
        return
    if isinstance(replacement, str):
        yield from _bad_text(root, replacement, plugin)
        return
    path = replacement_path(root, replacement)
    if path is None:
        yield "the replacement object is not mounted in the command tree"
        return
    target = root
    for name in path[1:]:
        target = target[name]
    if isinstance(mark_of(target), Deprecated):
        yield f"the replacement `{' '.join(path)}` is itself deprecated"


def _bad_text(root: App, text: str, plugin: str | None) -> Iterator[str]:
    if COMMAND_TEXT.match(text):
        words = text.split()[1:]
        current = root
        for word in words:
            name = resolve_command(current, word)
            if name is None and current is not root and current.default_command is not None:
                break  # the rest are the command's arguments (`untaped auth login awx`)
            if name is None:
                yield f"`{text}` does not resolve to a command"
                return
            current = current[name]
        if plugin is not None and words[0] == plugin:
            yield f"`{text}` names a command of the same plugin; pass the object"
    elif KEY_TEXT.match(text):
        keys = {
            ".".join(descriptor.path)
            for descriptor in walk_settings(get_settings_model(), include_collections=True)
        }
        if text not in keys:
            yield f"`{text}` is not a current setting"


# --- every deprecation names its removal; an SDK callable's two marks agree ----

_UNDATED = "every deprecation is a promise with a date: add removed_in=<major> to its mark"


def undated_violations(spec: ComposedPlugin, candidate: PluginCandidate) -> list[str]:
    """``undated-deprecation`` for each mark of ``spec``'s plugin that names no removal.

    Read from the surface lines of ``candidate``, its entry point
    (:func:`untaped.core.surface.plugin_lines`), so every kind the release
    check reads is covered by one walk: commands, groups, aliases, settings,
    renamed keys and cache renames. Its ``sdk`` lines are
    :func:`deprecation_text_violations`' to report.
    """
    from untaped.core.surface import DeprecatedMark, SurfaceError, plugin_lines  # noqa: PLC0415

    try:
        lines = plugin_lines(candidate)
    except SurfaceError as exc:
        return [
            f"{spec.name}::undated-deprecation::its marks can't be read from its surface "
            f"({exc}); untaped.sdk.testing.assert_surface shows the same"
        ]
    except Exception:
        # A contracts function that fails is the contracts row's finding.
        return []
    return [
        f"{line.identity}::undated-deprecation::{_UNDATED}"
        for line in lines
        if isinstance(line.mark, DeprecatedMark)
        and line.mark.removed_in is None
        and not line.identity.startswith("sdk ")
    ]


def deprecation_text_violations(module: ModuleType) -> list[str]:
    """``<module>.<qualname>::<rule>::<detail>`` for the deprecated SDK callables of ``module``.

    Each export (``__all__``, else its public names) and each member a class
    of the workspace defines is read: ``warnings.deprecated`` without
    untaped's mark, or a mark without a removal, is ``undated-deprecation``;
    a mark without the literal is ``untyped-deprecation``; a literal that is
    not the mark's sentence is ``deprecation-text``, naming the one to paste;
    a parameter marked in its annotation on a function without
    ``@deprecated_parameters`` is ``unwarned-parameter``. Nothing compares at
    import: the repo test and ``untaped lint`` run this before a merge.
    """
    package = module.__name__.partition(".")[0]
    found: list[str] = []
    for name, obj in _exports(module):
        if foreign(obj) and str(getattr(obj, "__module__", "")).partition(".")[0] != package:
            continue  # another library's deprecation is not this module's to date
        where = f"{module.__name__}.{name}"
        found.extend(f"{where}::{rule}::{detail}" for rule, detail in _callable_violations(obj))
        if isinstance(obj, type):
            for member, value in vars(obj).items():
                if member == "__init__" or not member.startswith("_"):
                    found.extend(
                        f"{where}.{member}::{rule}::{detail}"
                        for rule, detail in _callable_violations(value)
                    )
    return sorted(set(found))


def _exports(module: ModuleType) -> list[tuple[str, object]]:
    """``__all__``'s objects, else the public names ``module`` defines itself."""
    names = getattr(module, "__all__", None)
    if names is None:
        names = [
            name
            for name, obj in vars(module).items()
            if not name.startswith("_") and getattr(obj, "__module__", None) == module.__name__
        ]
    return [(name, getattr(module, name)) for name in sorted(set(names)) if hasattr(module, name)]


def _callable_violations(obj: object) -> Iterator[tuple[str, str]]:
    """The rules for one exported function, class or class member."""
    target = descriptor_function(obj)
    if isinstance(target, type):
        mark, literal = class_mark(target), vars(target).get("__deprecated__")
    elif inspect.isfunction(target):
        mark, literal = function_mark(target), getattr(target, "__deprecated__", None)
    else:
        return
    if literal is not None and not isinstance(mark, Deprecated):
        yield (
            "undated-deprecation",
            "carries warnings.deprecated and no untaped mark; add "
            "@deprecated(removed_in=<major>, replacement=...) above it so the surface dates it",
        )
    elif isinstance(mark, Deprecated):
        sentence = deprecation_sentence(target, mark)
        if mark.removed_in is None:
            yield "undated-deprecation", _UNDATED
        if literal is None:
            yield (
                "untyped-deprecation",
                "untaped's mark alone is invisible to type checkers; add "
                f"@warnings.deprecated({_literal(sentence)}) under it",
            )
        elif literal != sentence:
            yield (
                "deprecation-text",
                f"its warnings.deprecated text is not its mark's; use {_literal(sentence)}",
            )
    if not isinstance(target, type):
        yield from _parameter_violations(target, obj)


def _parameter_violations(
    function: Callable[..., Any], declared: object
) -> Iterator[tuple[str, str]]:
    marked, unreadable = read_parameter_marks(function)
    for name, problem in sorted(unreadable.items()):
        yield (
            "unwarned-parameter",
            f"{name}=: its deprecated(...) mark can't be evaluated where the function is "
            f"defined, so it never warns ({problem}); name only module-level objects in it",
        )
    for name, mark in sorted(marked.items()):
        if mark.removed_in is None:
            yield "undated-deprecation", f"{name}=: {_UNDATED}"
    if marked and not warns_parameters(declared):
        names = ", ".join(f"{name}=" for name in sorted(marked))
        fix = (
            "write the __init__ yourself, under @deprecated_parameters"
            # A dataclass's generated __init__ has no source to decorate.
            if getattr(getattr(function, "__code__", None), "co_filename", "") == "<string>"
            else "add @deprecated_parameters to the function"
        )
        yield (
            "unwarned-parameter",
            f"{names} is marked deprecated but nothing warns when it is passed; {fix}",
        )


def _literal(text: str) -> str:
    """``text`` as the double-quoted literal to paste."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
