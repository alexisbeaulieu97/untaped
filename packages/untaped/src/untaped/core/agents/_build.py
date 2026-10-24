"""Build the self-description model from the composed root app and the composition.

The map never resolves a lazy plugin: a plugin's line is its
``PluginSpec.help``, and its extension points and fills come from the
contracts registry (``every_offer``), so a filler that contract quarantine
refused never appears. A plugin's page and its command pages resolve that
plugin alone. Deprecated and hidden commands, options and settings are left
out, as ``--help`` leaves them out; asked for by name, a deprecated command
still answers.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterator, Sequence
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from cyclopts import App
from pydantic import SecretStr

from untaped.core.agents._docstring import describe, first_line, markdown
from untaped.core.agents._guides import guides
from untaped.core.agents._model import (
    Answerer,
    CommandLine,
    CommandPage,
    ExitCodeLine,
    Extends,
    ExtensionPoint,
    Fill,
    GuideLine,
    GuidePage,
    Map,
    Mark,
    Option,
    PluginEntry,
    PluginPage,
    Setting,
    Shared,
    Writes,
)
from untaped.core.agents._requirement import untaped_requirement
from untaped.core.surface._types import home_relative
from untaped.runtime.bootstrap import fail_if_unbuildable
from untaped.runtime.mount import declared
from untaped.runtime.registry import CompositionResult, RegisteredPlugin, owns_contracts
from untaped.runtime.settings import resolve_config_path
from untaped.sdk._cli import write_kind
from untaped.sdk.errors import EXIT_CODE_PRECEDENCE, ExitCode
from untaped.sdk.marks import (
    Deprecated,
    Experimental,
    Stability,
    children,
    class_mark,
    deprecated_aliases,
    mark_of,
    marked_options,
)
from untaped.sdk.words import shown_path

#: The name core answers to on the pages (``untaped agents untaped``); no plugin may take it.
CORE = "untaped"
#: The command these pages come from: it describes the others, not itself, on the map.
AGENTS = "agents"
#: Root options that don't change what a command does: left off the shared section.
_NOT_SHARED = frozenset({"--deprecated"})
_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.VAR_POSITIONAL,
)


# --- who answered ------------------------------------------------------------


def answerer(plugin: RegisteredPlugin | None = None) -> Answerer:
    """The version line's facts, and the project mismatch line when the folder asks for another."""
    version = metadata.version(CORE)
    distribution = None
    if plugin is not None:
        ref = plugin.plugin_ref
        distribution = " ".join(
            part for part in (ref.distribution, ref.distribution_version) if part
        )
    return Answerer(
        untaped=version,
        environment=shown_path(sys.prefix),
        plugin=distribution or None,
        mismatch=_mismatch(version),
    )


def _mismatch(version: str) -> str | None:
    found = untaped_requirement(Path.cwd())
    if found is None or found.satisfied_by(version):
        return None
    return (
        f"This project ({shown_path(found.file.parent)}) requires {found.requirement}; "
        f"this untaped is {version}. Run the project's own untaped, through the project's "
        "environment, instead."
    )


# --- stage 1: the map ----------------------------------------------------------


def build_map(root: App, result: CompositionResult) -> Map:
    """``untaped agents``: core's commands and every composed plugin, without resolving one."""
    points, fills = _contracts(result)
    core = tuple(
        CommandLine((name,), first_line(sub.help), _mark(mark_of(sub)))
        for name, sub in core_commands(root, result)
        if name != AGENTS
    )
    plugins = tuple(
        PluginEntry(
            name=plugin.spec.name,
            summary=_plugin_summary(plugin),
            mark=_mark(plugin.spec.stability),
            points=points.get(plugin.spec.name, ()),
            extends=tuple(sorted({owner for owner, _, _ in fills.get(plugin.spec.name, ())})),
        )
        for plugin in sorted(result.plugins, key=lambda each: each.spec.name)
    )
    return Map(answerer(), shared(root), core, plugins)


def shared(root: App) -> Shared:
    """What every command shares; each line generated from the code that makes it true."""
    options = tuple(
        option
        for argument in root.meta.assemble_argument_collection(parse_docstring=True)
        if argument.show
        and argument.field_info.kind is not inspect.Parameter.VAR_POSITIONAL
        and not _NOT_SHARED & set(argument.names)
        for option in (_option(argument),)
    )
    codes = tuple(
        ExitCodeLine(int(code), markdown(code.description).rstrip(".")) for code in ExitCode
    )
    return Shared(
        shown_path(resolve_config_path()),
        options,
        codes,
        tuple(int(code) for code in EXIT_CODE_PRECEDENCE),
    )


def core_commands(root: App, result: CompositionResult) -> Iterator[tuple[str, App]]:
    """The root's own commands, in mount order; plugins are never resolved here."""
    plugins = {plugin.spec.name for plugin in result.plugins}
    for name in root:
        if name.startswith("-") or name in plugins:
            continue
        sub = root[name]
        if sub.show is not False and not isinstance(mark_of(sub), Deprecated):
            yield name, sub


def _plugin_summary(plugin: RegisteredPlugin) -> str:
    if plugin.spec.help is not None:
        return first_line(plugin.spec.help)
    return "" if plugin.app is None else first_line(plugin.app.help)


# --- extension points -------------------------------------------------------------

type _Fills = dict[str, list[tuple[str, ExtensionPoint, str]]]
"""Per providing plugin: (owner, point, the provider's own line)."""


def _contracts(result: CompositionResult) -> tuple[dict[str, tuple[ExtensionPoint, ...]], _Fills]:
    """Each owner's extension points with their fills, and what each plugin fills."""
    if not any(plugin.spec.provides or owns_contracts(plugin.spec) for plugin in result.plugins):
        return {}, {}
    # Only the pages that show contracts pay for importing them.
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415
        Provider,
        every_offer,
        owned_contracts,
    )

    offers = [entry for entry in every_offer() if isinstance(entry, Provider)]
    point_of: dict[type, ExtensionPoint] = {}
    points: dict[str, tuple[ExtensionPoint, ...]] = {}
    for owner, infos in owned_contracts().items():
        for info in infos:
            fills = sorted(
                (
                    Fill(entry.plugin, first_line(type(entry.instance).__doc__))
                    for entry in offers
                    if entry.binding.contract.cls is info.cls
                ),
                key=lambda fill: fill.plugin,
            )
            point_of[info.cls] = ExtensionPoint(
                owner, first_line(info.cls.__doc__), tuple(fills), _mark(class_mark(info.cls))
            )
        points[owner] = tuple(point_of[info.cls] for info in infos)
    filled: _Fills = {}
    for entry in offers:
        filled.setdefault(entry.plugin, []).append(
            (
                entry.binding.owner,
                point_of[entry.binding.contract.cls],
                first_line(type(entry.instance).__doc__),
            )
        )
    return points, filled


# --- stage 2: one plugin -------------------------------------------------------


def build_plugin_page(root: App, result: CompositionResult, name: str) -> PluginPage:
    """``untaped agents PLUGIN``, or core's own page for ``untaped``."""
    if name == CORE:
        commands = [
            line
            for command, sub in core_commands(root, result)
            for line in _commands(sub, (command,), include_self=True)
        ]
        return PluginPage(
            answerer=answerer(),
            name=CORE,
            summary=first_line(root.help),
            mark=None,
            commands=tuple(commands),
            points=(),
            extends=(),
            settings_prefix=CORE,
            settings=_settings(result, None),
        )
    plugin = _plugin(result, name)
    points, fills = _contracts(result)
    app = None if plugin.spec.app_factory is None else _plugin_app(root, name)
    shadowing = top_names(app)
    return PluginPage(
        answerer=answerer(plugin),
        name=name,
        summary=_plugin_summary(plugin) if app is None else first_line(app.help),
        mark=_mark(plugin.spec.stability),
        commands=() if app is None else tuple(_commands(app, ())),
        points=points.get(name, ()),
        extends=tuple(Extends(point, line) for _, point, line in fills.get(name, ())),
        settings_prefix=name,
        settings=_settings(result, plugin),
        guides=tuple(
            GuideLine(guide.name, guide.description, guide.name in shadowing)
            for guide in guides(plugin.spec)
        ),
        own=None if app is None or app.default_command is None else _own(root, result, name, app),
    )


def _own(root: App, result: CompositionResult, name: str, app: App) -> CommandPage:
    """The plugin's own command: its usage, with the default command's docstring.

    The app's help is the plugin's line, so the behaviour and examples are
    read from the command the app runs.
    """
    page = build_command_page(root, result, name, ())
    described = describe(inspect.getdoc(app.default_command), None)
    return replace(page, summary=described.summary, body=described.body, subcommands=())


def top_names(app: App | None) -> frozenset[str]:
    """The names and aliases of ``app``'s top-level commands: a guide by one is shadowed."""
    return (
        frozenset() if app is None else frozenset(name for name in app if not name.startswith("-"))
    )


def build_guide_page(result: CompositionResult, name: str, guide: str) -> GuidePage:
    """``untaped agents PLUGIN GUIDE``; ``KeyError`` when the plugin has no guide by that name.

    A command by the same name wins: the caller asks for the command first.
    """
    plugin = _plugin(result, name)
    found = next((each for each in guides(plugin.spec) if each.name == guide), None)
    if found is None:
        raise KeyError(guide)
    return GuidePage(
        answerer=answerer(plugin),
        plugin=name,
        name=guide,
        description=found.description,
        body=found.body,
        mark=_mark(plugin.spec.stability),
    )


def _plugin(result: CompositionResult, name: str) -> RegisteredPlugin:
    return next(plugin for plugin in result.plugins if plugin.spec.name == name)


def _commands(
    app: App, prefix: tuple[str, ...], *, include_self: bool = False
) -> list[CommandLine]:
    """Every runnable command under ``app``, depth first, each with its summary and marks."""
    found: list[CommandLine] = []
    if include_self and app.default_command is not None:
        found.append(_line(app, prefix))
    for name, sub in visible_children(app):
        path = (*prefix, name)
        if sub.default_command is not None:
            found.append(_line(sub, path))
        found.extend(_commands(sub, path))
    return found


def _line(app: App, path: tuple[str, ...]) -> CommandLine:
    return CommandLine(
        path, first_line(app.help), _mark(mark_of(app)), _writes(app.default_command)
    )


def visible_children(app: App) -> Iterator[tuple[str, App]]:
    """The subcommands ``--help`` lists: no hidden, deprecated or renamed spelling."""
    renamed = deprecated_aliases(app)
    for name, sub in children(app, resolve=True):
        if name in renamed or sub.show is False or isinstance(mark_of(sub), Deprecated):
            continue
        yield name, sub


def _mark(stability: Stability | None) -> Mark:
    return "experimental" if isinstance(stability, Experimental) else None


# --- settings ------------------------------------------------------------------


def _settings(result: CompositionResult, plugin: RegisteredPlugin | None) -> tuple[Setting, ...]:
    """The plugin's settings (core's, for ``None``), keys relative to their section."""
    # The config reference's reader of the same sections; imported only for a plugin page.
    from untaped.core.surface._settings import sections, setting_leaves  # noqa: PLC0415

    if plugin is None:
        wanted = [
            section for section in sections(result, extensions=False) if section.prefix == CORE
        ]
        strip = f"{CORE}."
    else:
        wanted = [
            section
            for section in sections(result, extensions=False, root=False)
            if section.prefix == plugin.spec.name
        ]
        strip = f"{plugin.spec.name}."
    inherited = None if plugin is None else plugin.spec.stability
    rows: list[Setting] = []
    for section in wanted:
        if section.is_state:
            continue
        for leaf in setting_leaves(section):
            if isinstance(leaf.mark, Deprecated):
                continue
            choices = leaf.choices.names() if leaf.choices is not None else ()
            description = " ".join(
                part for part in (first_line(leaf.description), leaf.bounds) if part
            )
            rows.append(
                Setting(
                    key=leaf.key.removeprefix(strip),
                    type=_type_text(leaf.annotation, choices),
                    default=_default_text(leaf.default),
                    description=description,
                    mark=None if leaf.mark is inherited else _mark(leaf.mark),
                )
            )
    return tuple(rows)


def _type_text(annotation: Any, choices: Sequence[str]) -> str:
    origin = get_origin(annotation)
    if choices:
        listed = ", ".join(f"`{choice}`" for choice in choices)
        return f"mapping keyed by {listed}" if origin is dict else f"one of {listed}"
    if origin is Literal:
        return "one of " + ", ".join(f"`{value}`" for value in get_args(annotation))
    if annotation is SecretStr:
        return "secret"
    if origin in (list, tuple, set, frozenset):
        return "list"
    if origin is dict:
        return "mapping"
    if annotation is Path:
        return "path"
    names = {"str": "text", "int": "integer", "float": "number", "bool": "true/false"}
    name = getattr(annotation, "__name__", None)
    return names.get(name, name) if isinstance(name, str) else str(annotation)


def _default_text(value: object) -> str | None:
    """A default as a page writes it; ``None`` when there is nothing worth saying."""
    if value is None or value == "" or (isinstance(value, list | tuple | dict | set) and not value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | Path):
        return home_relative(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return None


def _plugin_app(root: App, name: str) -> App:
    """The plugin's resolved app; a plugin that could not build it fails as its commands do."""
    app = root[name]
    fail_if_unbuildable(app)
    return app


# --- stage 3: one command --------------------------------------------------------


def build_command_page(
    root: App, result: CompositionResult, name: str, path: Sequence[str]
) -> CommandPage:
    """``untaped agents PLUGIN COMMAND...``; ``KeyError`` when the path names no command."""
    plugin: RegisteredPlugin | None = None
    invocation: tuple[str, ...] = (CORE,)
    if name == CORE:
        app = root
        if path[0] in {each.spec.name for each in result.plugins}:
            raise KeyError(path[0])
    else:
        plugin = _plugin(result, name)
        if plugin.spec.app_factory is None:
            raise KeyError(path[0])
        app, invocation = _plugin_app(root, name), (CORE, name)
    inherited = None if plugin is None else plugin.spec.stability
    for word in path:
        if word.startswith("-"):
            raise KeyError(word)
        app = app[word]
        inherited = mark_of(app) or inherited
    described = describe(app.help, app.default_command)
    runs = app.default_command is not None
    return CommandPage(
        answerer=answerer(plugin),
        plugin=name,
        path=tuple(path),
        invocation=(*invocation, *path),
        summary=described.summary,
        body=described.body,
        mark=_mark(inherited),
        writes=_writes(app.default_command) if runs else None,
        runs=runs,
        options=_options(app) if runs else (),
        subcommands=tuple(_commands(app, tuple(path))),
        examples=described.examples,
    )


def _writes(func: object) -> Writes:
    """What a command says of its writes: ``@writes``, or a 12.0 command's ``Confirm``."""
    decl = declared(func)
    if decl is not None:
        return "asks" if decl.asks_first else None
    return write_kind(func)


def _options(app: App) -> tuple[Option, ...]:
    marked = {argument.field_info.name for argument, _ in marked_options(app)}
    return tuple(
        _option(argument)
        for argument in app.assemble_argument_collection(parse_docstring=True)
        if argument.show
        and argument.parse
        and not argument.children
        and argument.field_info.name not in marked
    )


def _option(argument: Any) -> Option:
    positional = argument.field_info.kind in _POSITIONAL
    count, consume_all = argument.token_count()
    switch = count == 0 and not consume_all
    names = tuple(argument.names)
    if not positional:
        names = tuple(
            sorted(names, key=lambda each: (not each.startswith("--"), each in argument.negatives))
        )
    return Option(
        names=names,
        help=markdown(" ".join(inspect.cleandoc(argument.parameter.help or "").split())),
        positional=positional,
        required=bool(argument.required),
        many=bool(consume_all) or count > 1,
        default=None if argument.required or switch else _shown_default(argument),
        choices=()
        if switch
        else tuple(str(each) for each in argument.get_choices(force=True) or ()),
    )


def _shown_default(argument: Any) -> str | None:
    """The default as ``--help`` would show it: the parameter's ``show_default``, if callable."""
    default = argument.field_info.default
    if default is inspect.Parameter.empty:
        return None
    show = argument.parameter.show_default
    if show is False:
        return None
    if callable(show):
        return str(show(default))
    return _default_text(getattr(default, "value", default))
