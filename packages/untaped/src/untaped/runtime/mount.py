"""The mount: a composed 12.0 plugin's commands, parsed from the declaration and run.

Composition (:mod:`untaped.runtime.composition`) reads the plugin classes into
a :class:`~untaped.runtime.declaration.Declaration`; the mount makes that
declaration a command line. Each command's options become the parameters of
one generated Cyclopts command, whose call turns the tokens given into the
command's data, validates it as the command class, fills the fields core
owns from their flags, runs ``run`` under the invocation's run
(:func:`untaped.runtime.run.build_run`) and prints what it returns.

Until core reads the declaration itself (12.0 delivery step 4), a composed
plugin reaches the rest of composition as the :class:`PluginSpec` core already
reads (:func:`plugin_spec`), so ``config``, ``plugin list``, ``skills`` and
``doctor`` see it like any other plugin.
"""

from __future__ import annotations

import inspect
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property, partial
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeGuard, cast

from cyclopts import App, Group, Parameter
from pydantic import BaseModel, ValidationError

from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import (
    CommandDecl,
    Declaration,
    GroupDecl,
    OptionDecl,
    StabilityDecl,
    target_of,
)
from untaped.runtime.registry import PluginSpec, SkillAsset, resolve_target
from untaped.runtime.run import build_run
from untaped.sdk._block import OR_STDIN, FieldBlock
from untaped.sdk._cli import _format_default as format_default
from untaped.sdk._cli import emit, report_errors
from untaped.sdk.diagnostic import WireModel
from untaped.sdk.errors import UsageError, first_validation_error
from untaped.sdk.fields import Output, Stdin, column_fields
from untaped.sdk.marks import (
    DEPRECATED_OPTIONS,
    Deprecated,
    Stability,
    deprecated,
    deprecated_alias,
    experimental,
    mark_app,
)
from untaped.sdk.plugin import Command, Plugin, field_block, generic_argument
from untaped.sdk.plugin import Group as CommandGroup
from untaped.sdk.records import Record
from untaped.sdk.run import run_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic.fields import FieldInfo

    from untaped.runtime.theme import OutputFormat


@dataclass(frozen=True)
class Mount:
    """One composition of plugin classes: the declaration and the classes it was read from."""

    declaration: Declaration
    #: The composed plugin classes, by plugin name.
    plugins: Mapping[str, type[Plugin]]

    @cached_property
    def commands(self) -> Mapping[str, type[Command[Any]]]:
        """Every command class the plugins reach, by its target."""
        found: dict[str, type[Command[Any]]] = {}

        def walk(nodes: Sequence[object]) -> None:
            for node in nodes:
                if _is_group(node):
                    walk(node.commands)
                elif isinstance(node, type) and issubclass(node, Command):
                    found.setdefault(target_of(node), node)

        for cls in self.plugins.values():
            walk((*cls.commands, *((cls.runs,) if cls.runs is not None else ())))
        return found

    def command(self, decl: CommandDecl) -> type[Command[Any]]:
        """The class ``decl`` was read from."""
        return self.commands[decl.target]

    def kinds_under(self, models: Sequence[type[BaseModel]]) -> dict[str, type[BaseModel]]:
        """Each declared kind whose class is one of ``models`` or a subclass of one."""
        found: dict[str, type[BaseModel]] = {}
        for kind in self.declaration.kinds:
            try:
                cls = resolve_target(kind.target)
            except Exception:
                continue
            if _is_model(cls) and issubclass(cls, tuple(models)):
                found[kind.kind] = cls
        return found

    def kind_of(self, model: type[BaseModel]) -> str | None:
        """The declared kind of ``model``, if a plugin prints or reads it."""
        target = target_of(model)
        return next((k.kind for k in self.declaration.kinds if k.target == target), None)


def is_plugin_class(obj: object) -> TypeGuard[type[Plugin]]:
    """Whether ``obj`` is a 12.0 plugin class (what an entry point may name besides a spec)."""
    return isinstance(obj, type) and issubclass(obj, Plugin)


def _is_group(obj: object) -> TypeGuard[type[CommandGroup]]:
    return isinstance(obj, type) and issubclass(obj, CommandGroup)


def _is_model(obj: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


def compose_plugins(entries: Sequence[PluginEntry]) -> Mount:
    """Compose the plugin classes of ``entries`` in one walk."""
    declaration = compose(entries)
    by_name = {entry.name: entry.plugin for entry in entries}
    plugins: dict[str, type[Plugin]] = {}
    for plugin in declaration.plugins:
        cls = by_name[plugin.name]
        if is_plugin_class(cls):
            plugins[plugin.name] = cls
    return Mount(declaration=declaration, plugins=plugins)


def plugin_spec(mount: Mount, name: str) -> PluginSpec:
    """The composed plugin ``name`` as the :class:`PluginSpec` the rest of composition reads.

    Its help is the plugin's summary, so its commands are mounted lazily: the
    Cyclopts app is built from the declaration the first time it is dispatched.
    """
    plugin = mount.declaration.plugin(name)
    has_commands = bool(plugin.commands) or plugin.runs is not None
    skills = (SkillAsset(name=f"untaped-{name}", description=plugin.skill),) if plugin.skill else ()
    return PluginSpec(
        name=name,
        app_factory=partial(build_app, mount, name) if has_commands else None,
        settings=mount.plugins[name].settings,
        help=plugin.summary if has_commands else None,
        stability=_mark(plugin.stability),
        skills=skills,
    )


# --- the command line ----------------------------------------------------------


def build_app(mount: Mount, name: str) -> App:
    """The Cyclopts app of plugin ``name``: its groups and commands, from the declaration."""
    plugin = mount.declaration.plugin(name)
    app = App(name=name, help=plugin.doc)
    _add(app, mount, plugin.commands)
    if plugin.runs is not None:
        app.default(_command(mount, plugin.runs))
    return app


def _add(app: App, mount: Mount, nodes: Sequence[CommandDecl | GroupDecl]) -> None:
    for node in nodes:
        if isinstance(node, GroupDecl):
            sub = App(name=node.name, help=node.doc)
            mark = _mark(node.stability)
            if mark is not None:
                mark_app(sub, mark, source="own")
            _add(sub, mount, node.commands)
            app.command(sub)
            continue
        app.command(_command(mount, node), name=node.name, help=node.doc)
        for alias in node.aliases:
            deprecated_alias(app, alias.name, node.name, removed_in=alias.removed_in)


_TOKENS: Mapping[str, type] = {"str": str, "int": int, "float": float, "bool": bool}


def _command(mount: Mount, decl: CommandDecl) -> Callable[..., None]:
    """The generated Cyclopts command for ``decl``: one parameter per option, in order."""
    panels: dict[str, Group] = {}
    positionals: list[inspect.Parameter] = []
    flags: list[inspect.Parameter] = []
    options: dict[str, OptionDecl] = {}
    for index, option in enumerate(decl.options):
        name = f"_{index}"
        options[name] = option
        panel = panels.setdefault(option.panel, Group(option.panel, sort_key=len(panels)))
        annotation, default = _parameter(option, panel)
        kind = (
            inspect.Parameter.POSITIONAL_ONLY
            if option.positional
            else inspect.Parameter.KEYWORD_ONLY
        )
        target = positionals if option.positional else flags
        target.append(inspect.Parameter(name, kind, annotation=annotation, default=default))
    signature = inspect.Signature([*positionals, *flags])

    def command(*args: object, **kwargs: object) -> None:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        given = [(options[name], value) for name, value in bound.arguments.items()]
        _dispatch(mount, decl, given)

    command.__signature__ = signature  # type: ignore[attr-defined]
    command.__annotations__ = {p.name: p.annotation for p in signature.parameters.values()}
    command.__name__ = command.__qualname__ = decl.name.replace("-", "_")
    command.__doc__ = decl.doc
    mark = _mark(decl.stability)
    return mark(command) if mark is not None else command


def _parameter(option: OptionDecl, panel: Group) -> tuple[object, object]:
    """The annotation and default of the parameter standing for ``option``."""
    value: Any = _TOKENS[option.token.removeprefix("list[").removesuffix("]")]
    if option.choices:
        choices = (
            tuple(int(choice) for choice in option.choices) if value is int else option.choices
        )
        value = Literal[choices]
    if option.token.startswith("list["):
        value = list[value]
    settings: dict[str, Any] = {
        "help": option.help,
        "group": panel,
        # ``-`` is a value for an option that reads stdin with it
        "allow_leading_hyphen": option.allow_hyphen or option.or_stdin,
    }
    metadata: list[object] = []
    if option.positional:
        settings["name"] = option.metavar
    else:
        settings["name"] = tuple(n for n in (option.flag, option.short) if n is not None)
        settings["negative"] = (option.negative,) if option.negative else ()
        if option.token.startswith("list["):
            settings["consume_multiple"] = option.flag != "--columns"
        mark = _mark(option.stability)
        if isinstance(mark, Deprecated):
            settings["group"] = DEPRECATED_OPTIONS
            metadata.append(mark)
    if option.flag == "--format":
        settings["show_default"] = format_default
        return Annotated[value, Parameter(**settings), *metadata], "table"
    if option.required and option.block is None:
        return Annotated[value, Parameter(**settings), *metadata], inspect.Parameter.empty
    shown = option.default not in (None, False, [], "")
    settings["show_default"] = (lambda _value: _shown(option.default)) if shown else False
    return Annotated[value | None, Parameter(**settings), *metadata], None


def _shown(default: object) -> str:
    if isinstance(default, list):
        return ", ".join(str(each) for each in default)
    return str(default)


def _mark(stability: StabilityDecl | None) -> Stability | None:
    if stability is None:
        return None
    if stability.stability == "experimental":
        return experimental
    return deprecated(removed_in=stability.removed_in, replacement=stability.replacement)


# --- dispatch ------------------------------------------------------------------


def _dispatch(mount: Mount, decl: CommandDecl, given: Sequence[tuple[OptionDecl, object]]) -> None:
    """Build the command from the tokens given, run it under the run, print what it returns."""
    with report_errors():
        cls = mount.command(decl)
        command = _instance(mount, decl, cls, given)
        with run_scope(build_run(mount.declaration, mount.plugins)):
            _print(mount, decl, command, command.run())


def _instance(
    mount: Mount,
    decl: CommandDecl,
    cls: type[Command[Any]],
    given: Sequence[tuple[OptionDecl, object]],
) -> Command[Any]:
    """The command ``cls`` validated from what was given; a bad value is exit 2 naming its flag."""
    data: dict[str, Any] = {}
    blocks: defaultdict[str, dict[str, object]] = defaultdict(dict)
    for option, value in given:
        if option.block is not None:
            blocks[option.field][option.flag or ""] = value
        elif value is not None:
            if option.or_stdin and value == "-" and _annotation(cls, option.field) is str:
                value = sys.stdin.read()
            _put(data, option.field, value)
    for path, flags in blocks.items():
        info = _field(cls, path)
        _put(data, path, _filled(mount, info, flags))
    for path in _bundles(decl):
        if _get(data, path) is None and _field(cls, path).is_required():
            _put(data, path, {})
    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        raise UsageError(_usage(decl, exc)) from exc


def _filled(mount: Mount, info: FieldInfo, flags: Mapping[str, object]) -> FieldBlock:
    """The core field ``info`` declares, filled from its flags for this invocation."""
    block = field_block(info.annotation)
    assert block is not None  # the declaration's block options come from core fields
    default = info.default if isinstance(info.default, block) else block()
    if isinstance(default, Output):
        model = generic_argument(info.annotation)
        columns = column_fields(model) if isinstance(model, type) else ()
        requested = flags.get("--columns")
        return default._filled(
            fmt=str(flags.get("--format") or "table"),
            requested=list(requested) if isinstance(requested, list) else None,
            columns=columns,
        )
    if isinstance(default, Stdin):
        models = _models(generic_argument(info.annotation))
        accepts_any = any(model in (BaseModel, WireModel, Record) for model in models)
        return default._filled(
            requested=bool(flags.get("--stdin")),
            models=models,
            kinds=None if accepts_any else mount.kinds_under(models),
        )
    return default


def _models(accepted: object) -> tuple[type[BaseModel], ...]:
    """The models ``Stdin[R]`` names: ``R``, or each member of a union."""
    if isinstance(accepted, type) and issubclass(accepted, BaseModel):
        return (accepted,)
    return tuple(
        arg
        for arg in getattr(accepted, "__args__", ())
        if isinstance(arg, type) and issubclass(arg, BaseModel)
    )


def _field(cls: type[BaseModel], path: str) -> FieldInfo:
    """The field at ``path`` (``selection.names``) of ``cls``, through its bundles."""
    model: type[BaseModel] = cls
    *bundles, last = path.split(".")
    for name in bundles:
        annotation = model.model_fields[name].annotation
        model = _bundle_model(annotation)
    return model.model_fields[last]


def _bundle_model(annotation: object) -> type[BaseModel]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in getattr(annotation, "__args__", ()):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    raise TypeError(f"{annotation!r} is not a bundle")


def _annotation(cls: type[BaseModel], path: str) -> object:
    """The value type of the option at ``path``: ``str`` for a ``TextOrStdin``."""
    info = _field(cls, path)
    return info.annotation if OR_STDIN in info.metadata else None


def _bundles(decl: CommandDecl) -> list[str]:
    """Every bundle path the options sit under, outermost first."""
    paths: dict[str, None] = {}
    for option in decl.options:
        parts = option.field.split(".")[:-1]
        for end in range(1, len(parts) + 1):
            paths[".".join(parts[:end])] = None
    return list(paths)


def _put(data: dict[str, Any], path: str, value: object) -> None:
    *bundles, last = path.split(".")
    for name in bundles:
        data = data.setdefault(name, {})
    data[last] = value


def _get(data: Mapping[str, Any], path: str) -> object:
    current: object = data
    for name in path.split("."):
        if not isinstance(current, Mapping) or name not in current:
            return None
        current = current[name]
    return current


def _usage(decl: CommandDecl, exc: ValidationError) -> str:
    """A validation error as the flag or argument it is about, and what is wrong."""
    error = exc.errors()[0]
    path = ".".join(str(part) for part in error.get("loc", ()) if not isinstance(part, int))
    option = next(
        (o for o in decl.options if o.field == path and o.block is None),
        None,
    )
    if option is None:
        return first_validation_error(exc)
    message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{option.flag or option.metavar}: {message}"


# --- printing --------------------------------------------------------------------


def _print(mount: Mount, decl: CommandDecl, command: Command[Any], result: object) -> None:
    """Print what ``run`` returned, as its return type declares."""
    if decl.returns == "nothing" or result is None:
        return
    if decl.returns in ("line", "line-or-none"):
        print(result)
        return
    output = _output(decl, command)
    records: BaseModel | list[BaseModel] = (
        result if isinstance(result, BaseModel) else list(cast("Iterable[BaseModel]", result))
    )
    renderer = output.render.get(output.format)
    if renderer is not None:
        renderer(records)
        return
    rows = [records] if isinstance(records, BaseModel) else records
    kinds = {mount.kind_of(type(row)) for row in rows} or {decl.output}
    emit(
        records,
        fmt=cast("OutputFormat", output.format),  # a plugin renderer's format returned above
        columns=output.requested,
        empty=output.empty_text,
        kind=kinds.pop() if len(kinds) == 1 else None,
        table_columns=list(output.columns) or None,
    )


def _output(decl: CommandDecl, command: Command[Any]) -> Output[Any]:
    """The ``Output`` core filled on ``command``."""
    path = next(option.field for option in decl.options if option.block == "output")
    value: object = command
    for name in path.split("."):
        value = getattr(value, name)
    if not isinstance(value, Output):
        raise TypeError(f"{decl.target} has no filled Output at {path}")
    return value


__all__ = ["Mount", "build_app", "compose_plugins", "plugin_spec"]
