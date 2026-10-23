"""The mount: a composed 12.0 plugin's commands, parsed from the declaration and run.

Composition (:mod:`untaped.runtime.composition`) reads the plugin classes into
a :class:`~untaped.runtime.declaration.Declaration`; the mount makes that
declaration a command line. Each command's options become the parameters of
one generated Cyclopts command, whose call turns the tokens given into the
command's data, validates it as the command class, has each field core owns
fill itself from its flags (:meth:`~untaped.sdk._block.FieldBlock._fill`), runs
``run`` under the invocation's run (:func:`untaped.runtime.run.build_run`) and
prints what it returns.

Until core reads the declaration itself (12.0 delivery step 4), a composed
plugin reaches the rest of composition as the :class:`PluginSpec` core already
reads (:func:`plugin_spec`), so ``config``, ``plugin list``, ``skills`` and
``doctor`` see it like any other plugin.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property, partial
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeGuard, cast, get_args, get_origin

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
from untaped.sdk._block import FieldBlock, Invocation
from untaped.sdk._cli import _format_default as format_default
from untaped.sdk._cli import report_errors
from untaped.sdk._stdin import read_stdin_text
from untaped.sdk.diagnostic import record_kinds
from untaped.sdk.errors import UsageError, first_validation_error
from untaped.sdk.fields import Output
from untaped.sdk.marks import (
    DEPRECATED_OPTIONS,
    Deprecated,
    Stability,
    deprecated,
    deprecated_alias,
    experimental,
    mark_app,
)
from untaped.sdk.plugin import Command, Plugin, field_block
from untaped.sdk.plugin import Group as CommandGroup
from untaped.sdk.run import run_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic.fields import FieldInfo


@dataclass(frozen=True)
class Mount:
    """One composition of plugin classes: the declaration and the classes it was read from."""

    declaration: Declaration
    #: The composed plugin classes, by plugin name.
    plugins: Mapping[str, type[Plugin]]

    @cached_property
    def commands(self) -> Mapping[tuple[str, ...], type[Command[Any]]]:
        """Every command class, by its words: the declaration walked beside the classes.

        A generated command (``derive``) shares its target with every other one
        of its name, so the words, never the target, say which class runs.
        """
        found: dict[tuple[str, ...], type[Command[Any]]] = {}

        def walk(decls: Sequence[CommandDecl | GroupDecl], nodes: Sequence[object]) -> None:
            # A composed plugin had no problem, so every node has its declaration.
            for decl, node in zip(decls, nodes, strict=True):
                if isinstance(decl, GroupDecl) and _is_group(node):
                    walk(decl.commands, node.commands)
                elif isinstance(decl, CommandDecl) and _is_command(node):
                    found[decl.path] = node

        for plugin in self.declaration.plugins:
            cls = self.plugins[plugin.name]
            walk(plugin.commands, cls.commands)
            if plugin.runs is not None and _is_command(cls.runs):
                found[plugin.runs.path] = cls.runs
        return found

    def command(self, decl: CommandDecl) -> type[Command[Any]]:
        """The class ``decl`` was read from."""
        return self.commands[decl.path]

    @cached_property
    def kinds(self) -> Mapping[str, type[BaseModel]]:
        """Every record kind core knows and its model: the declaration's and 11.x's."""
        found: dict[str, type[BaseModel]] = dict(record_kinds())
        for kind in self.declaration.kinds:
            try:
                cls = resolve_target(kind.target)
            except Exception:
                continue
            if _is_model(cls):
                found[kind.kind] = cls
        return found

    def kind_of(self, model: type[BaseModel]) -> str | None:
        """The declared kind of ``model``, if a plugin prints or reads it."""
        target = target_of(model)
        return next((k.kind for k in self.declaration.kinds if k.target == target), None)


def _is_group(obj: object) -> TypeGuard[type[CommandGroup]]:
    return isinstance(obj, type) and issubclass(obj, CommandGroup)


def _is_command(obj: object) -> TypeGuard[type[Command[Any]]]:
    return isinstance(obj, type) and issubclass(obj, Command)


def _is_model(obj: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


def compose_plugins(entries: Sequence[PluginEntry]) -> Mount:
    """Compose the plugin classes of ``entries`` (each a :class:`Plugin` subclass) in one walk."""
    declaration = compose(entries)
    by_name = {entry.name: cast("type[Plugin]", entry.plugin) for entry in entries}
    plugins = {plugin.name: by_name[plugin.name] for plugin in declaration.plugins}
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
    cls = mount.command(decl)
    panels: dict[str, Group] = {}
    positionals: list[inspect.Parameter] = []
    flags: list[inspect.Parameter] = []
    options: dict[str, OptionDecl] = {}
    for index, option in enumerate(decl.options):
        name = f"_{index}"
        options[name] = option
        panel = panels.setdefault(option.panel, Group(option.panel, sort_key=len(panels)))
        required = option.required and _bundles_required(cls, option.field)
        annotation, default = _parameter(option, panel, required=required)
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


def _parameter(option: OptionDecl, panel: Group, *, required: bool) -> tuple[object, object]:
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
            # one value per flag, as `--case a --case b`, so a positional after it stays one
            settings["consume_multiple"] = False
        mark = _mark(option.stability)
        if isinstance(mark, Deprecated):
            settings["group"] = DEPRECATED_OPTIONS
            metadata.append(mark)
    if option.flag == "--format":
        settings["show_default"] = format_default
        return Annotated[value, Parameter(**settings), *metadata], "table"
    if required and option.block is None:
        return Annotated[value, Parameter(**settings), *metadata], inspect.Parameter.empty
    shown = option.default is not None and option.default is not False
    shown = shown and option.default not in ([], "")
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
        with run_scope(build_run(mount.declaration, mount.plugins)):
            command = _instance(mount, decl, cls, given)
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
            if option.or_stdin and value == "-" and _value_type(cls, option.field) is str:
                value = read_stdin_text()
            _put(data, cls, option.field, value)
    for path, flags in blocks.items():
        info = _field(cls, path)
        _put(data, cls, path, _filled(mount, decl, info, flags))
    for path in _bundles(decl):
        if _get(data, cls, path) is None and _field(cls, path).is_required():
            _put(data, cls, path, {})
    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        raise UsageError(_usage(decl, exc)) from exc


def _filled(
    mount: Mount, decl: CommandDecl, info: FieldInfo, flags: Mapping[str, object]
) -> FieldBlock:
    """The core field ``info`` declares, filled by itself from its flags for this invocation."""
    block = field_block(info.annotation)
    assert block is not None  # the declaration's block options come from core fields
    default = info.default if isinstance(info.default, block) else block()
    call = Invocation(
        words=("untaped", *decl.path),
        annotation=info.annotation,
        kinds=lambda: mount.kinds,
    )
    return default._fill(flags, call)


def _field(cls: type[BaseModel], path: str) -> FieldInfo:
    """The field at ``path`` (``selection.names``) of ``cls``, through its bundles."""
    model, last = _owner(cls, path)
    return model.model_fields[last]


def _owner(cls: type[BaseModel], path: str) -> tuple[type[BaseModel], str]:
    """The bundle model holding the field at ``path``, and the field's name."""
    model: type[BaseModel] = cls
    *bundles, last = path.split(".")
    for name in bundles:
        model = _bundle_model(model.model_fields[name].annotation)
    return model, last


def _bundle_model(annotation: object) -> type[BaseModel]:
    if _is_model(annotation):
        return annotation
    for arg in get_args(annotation):
        if _is_model(arg):
            return arg
    raise TypeError(f"{annotation!r} is not a bundle")


def _bundles_required(cls: type[BaseModel], path: str) -> bool:
    """Whether every bundle around the field at ``path`` is required (none: ``True``)."""
    parts = path.split(".")[:-1]
    return all(_field(cls, ".".join(parts[: end + 1])).is_required() for end in range(len(parts)))


def _value_type(cls: type[BaseModel], path: str) -> object:
    """The value type of the field at ``path``, ``Annotated`` and ``| None`` peeled."""
    annotation: object = _field(cls, path).annotation
    while True:
        if get_origin(annotation) is typing.Annotated:
            annotation = get_args(annotation)[0]
        elif get_origin(annotation) in (typing.Union, types.UnionType):
            members = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(members) != 1:
                return annotation
            annotation = members[0]
        else:
            return annotation


def _bundles(decl: CommandDecl) -> list[str]:
    """Every bundle path the options sit under, outermost first."""
    paths: dict[str, None] = {}
    for option in decl.options:
        parts = option.field.split(".")[:-1]
        for end in range(1, len(parts) + 1):
            paths[".".join(parts[:end])] = None
    return list(paths)


def _keys(cls: type[BaseModel], path: str) -> list[str]:
    """The data keys of the field at ``path``: each step's alias when it has one."""
    keys: list[str] = []
    parts = path.split(".")
    for end in range(len(parts)):
        model, name = _owner(cls, ".".join(parts[: end + 1]))
        keys.append(model.model_fields[name].alias or name)
    return keys


def _put(data: dict[str, Any], cls: type[BaseModel], path: str, value: object) -> None:
    *bundles, last = _keys(cls, path)
    for key in bundles:
        data = data.setdefault(key, {})
    data[last] = value


def _get(data: Mapping[str, Any], cls: type[BaseModel], path: str) -> object:
    current: object = data
    for key in _keys(cls, path):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _usage(decl: CommandDecl, exc: ValidationError) -> str:
    """A validation error as the flag or argument it is about, and what is wrong."""
    error = exc.errors()[0]
    path = ".".join(str(part) for part in error.get("loc", ()) if not isinstance(part, int))
    option = next((o for o in decl.options if o.field == path and o.block is None), None)
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
    records: BaseModel | list[BaseModel] = (
        result if isinstance(result, BaseModel) else list(cast("Iterable[BaseModel]", result))
    )
    rows = [records] if isinstance(records, BaseModel) else records
    # a row of a subclass no plugin declares is the command's kind
    kinds = {mount.kind_of(type(row)) or decl.output for row in rows} or {decl.output}
    _output(decl, command)._print(records, kind=kinds.pop() if len(kinds) == 1 else None)


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
