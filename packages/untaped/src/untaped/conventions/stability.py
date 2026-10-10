"""Stability rules: experimental and deprecated marks are placed and worded by core.

Checks every command of a subtree against ``docs/plugins.md``:

- ``hand-typed-mark`` — an ``Experimental:`` or ``Deprecated:`` line in an app
  help, a command docstring, a visible parameter's help or a settings field's
  description (core writes the line from the mark; a hidden option's help is
  exempt);
- ``wrong-deprecated`` — a command function carries Python's
  ``warnings.deprecated``, or a settings field pydantic's ``deprecated=``,
  instead of untaped's ``@deprecated(...)``;
- ``nested-mark`` — a mark under a mark that makes it redundant or
  contradictory: experimental under experimental, or anything under
  deprecated (deprecated under experimental is allowed); a settings field
  sits under its plugin;
- ``bad-replacement`` — a ``deprecated`` replacement that is not mounted, is
  itself deprecated, or is text naming a command that does not resolve, a
  command of the plugin itself (pass the object), or a setting that does
  not exist (a setting's replacement is always text);
- ``mark-on-spec`` — a plugin's top app carries a mark of its own, which a
  lazy mount cannot see (mark the ``PluginSpec`` instead).

A group named after a stability panel is ``reserved-panel``, one of the help
tree's rules (:mod:`untaped.conventions.help_tree`).

Lines are ``<command path or setting key>::<rule>::<detail>``; they carry no
source line, so no inline marker can suppress them.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterable, Iterator

from cyclopts import App

from untaped._root_options import resolve_command
from untaped.config_schema import walk_settings
from untaped.plugins.registry import CompositionResult, PluginSpec
from untaped.settings import get_profile_settings_model, profile_section_models
from untaped.stability import (
    COMMAND_TEXT,
    KEY_TEXT,
    Deprecated,
    Experimental,
    Mark,
    Stability,
    app_mark,
    children,
    field_descriptions,
    mark_of,
    marks,
    pydantic_deprecated_fields,
    replacement_path,
)

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
    spec: PluginSpec | None = None,
    sections: Iterable[str] = (),
) -> list[str]:
    """``["<command path>::<rule>::<detail>", ...]`` for the subtrees ``root[name]``.

    ``spec`` is the checked plugin's, for the rule that needs it; its
    settings section is checked too, as are the ``sections`` named (core's).
    The marks come from :func:`untaped.stability.marks` over the whole
    composition: the checked plugin's subtree is resolved by ``root[name]``;
    a lazy sibling is never imported (its spec mark is still seen).
    """
    wanted = frozenset(names)
    settings = frozenset(sections) | ({spec.name} if spec is not None else frozenset())
    found: list[str] = []
    for top in sorted(wanted):
        subtree = root[top]
        if spec is not None:
            found.extend(_spec_violations(spec, subtree))
        for path, app in [((top,), subtree), *_walk(subtree, (top,))]:
            where = " ".join(path)
            found.extend(f"{where}::hand-typed-mark::{detail}" for detail in _hand_typed(app))
            if getattr(app.default_command, "__deprecated__", None) is not None:
                found.append(f"{where}::wrong-deprecated::{path[-1]} uses warnings.deprecated")
    every = marks(root, result)
    found.extend(_setting_violations(root, result, settings, every))
    for mark in (mark for mark in every if mark.target != "setting"):
        if mark.where.split()[0] in wanted:
            found.extend(f"{mark.where}::nested-mark::{d}" for d in _nested(mark, every))
            if isinstance(mark.stability, Deprecated):
                found.extend(
                    f"{mark.where}::bad-replacement::{d}"
                    for d in _bad_replacement(root, mark.stability, spec.name if spec else None)
                )
    return sorted(found)


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


def _spec_violations(spec: PluginSpec, top: App) -> Iterator[str]:
    entry = app_mark(top)
    if entry is not None and entry.source == "own":
        yield f"{spec.name}::mark-on-spec::{spec.name}"
    if spec.help is not None and _HAND_TYPED.search(spec.help):
        yield f"{spec.name}::hand-typed-mark::spec help"


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
        if argument.show and argument.parse:
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
            if name is None:
                yield f"`{text}` does not resolve to a command"
                return
            current = current[name]
        if plugin is not None and words[0] == plugin:
            yield f"`{text}` names a command of the same plugin; pass the object"
    elif KEY_TEXT.match(text):
        keys = {
            ".".join(descriptor.path)
            for descriptor in walk_settings(get_profile_settings_model(), include_collections=True)
        }
        if text not in keys:
            yield f"`{text}` is not a current setting"
