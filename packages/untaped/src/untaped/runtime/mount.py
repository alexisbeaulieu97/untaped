"""The mount: a composed 12.0 plugin's commands, parsed from the declaration and run.

Composition (:mod:`untaped.runtime.composition`) reads the plugin classes into
a :class:`~untaped.runtime.declaration.Declaration`; the mount makes that
declaration a command line. Each command's options become the parameters of
one generated Cyclopts command, whose call turns the tokens given into the
command's data, validates it as the command class, has each field core owns
fill itself from its flags (:meth:`~untaped.sdk._block.FieldBlock._fill`), runs
``run`` under the invocation's run (one the mount builds only when no
invocation opened one: :func:`untaped.runtime.run.current_or_built`) and
prints what it returns.

Until core reads the declaration itself (12.0 delivery step 4), a composed
plugin reaches the rest of composition as the :class:`PluginSpec` core already
reads (:func:`plugin_spec`), so ``config``, ``plugin list``, ``skills`` and
``doctor`` see it like any other plugin.
"""

from __future__ import annotations

import functools
import inspect
import types
import typing
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import timedelta
from functools import cached_property, partial
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeGuard, cast, get_args, get_origin

from cyclopts import App, Group, Parameter
from pydantic import BaseModel, ValidationError

from untaped.runtime.declaration import (
    BlockDecl,
    CommandDecl,
    Declaration,
    GroupDecl,
    OptionDecl,
    PluginDecl,
    Problem,
    Quarantined,
    StabilityDecl,
    target_of,
)
from untaped.runtime.registry import DeclaredBlocks, PluginSpec, SkillAsset, resolve_target
from untaped.sdk._block import FieldBlock, Invocation, OwnedBlock
from untaped.sdk._cli import _format_default as format_default
from untaped.sdk._cli import report_errors
from untaped.sdk._run import Run
from untaped.sdk._stdin import read_stdin_text
from untaped.sdk.diagnostic import record_kinds
from untaped.sdk.errors import Failed, Matched, UntapedError, UsageError, first_validation_error
from untaped.sdk.fields import Confirm, DryRun, File, Output
from untaped.sdk.marks import (
    DEPRECATED_OPTIONS,
    Deprecated,
    Stability,
    deprecated,
    deprecated_alias,
    experimental,
    mark_app,
)
from untaped.sdk.plugin import Command, Plugin, field_block, held_by
from untaped.sdk.plugin import Group as CommandGroup
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.dir import Dir

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic.fields import FieldInfo

    from untaped.runtime.composition import PluginEntry


@dataclass(frozen=True)
class Mount:
    """One composition of plugin classes: the declaration, and each class imported when needed.

    A start reads the declaration (from core's declaration cache, or composed
    just now) and imports no plugin class: dispatch imports the one whose
    command runs, ``doctor`` and ``setup`` the ones whose blocks they read.
    """

    declaration: Declaration
    #: The composed plugin classes, by plugin name, each imported on first read.
    plugins: Mapping[str, type[Plugin]]
    #: Each composed plugin's settings model, from its own (leaf) module.
    settings: Mapping[str, type[BaseModel]]
    #: Every problem composition found in each plugin it refused, by plugin name.
    problems: dict[str, tuple[Problem, ...]] = field(default_factory=dict, compare=False)
    _found: dict[str, Mapping[tuple[str, ...], type[Command[Any]]]] = field(
        default_factory=dict, compare=False, repr=False
    )

    @classmethod
    def of(
        cls, declaration: Declaration, classes: Mapping[str, type[Plugin]] | None = None
    ) -> Mount:
        """The mount of ``declaration``, with ``classes`` already imported (by plugin name)."""
        classes = classes or {}
        plugins = _Loaded(
            [plugin.name for plugin in declaration.plugins],
            partial(_plugin_class, declaration),
            dict(classes),
        )
        settings = _Loaded(
            [plugin.name for plugin in declaration.plugins],
            partial(_settings_model, declaration, plugins),
            {name: each.settings for name, each in classes.items()},
        )
        return cls(declaration=declaration, plugins=plugins, settings=settings)

    def kept(self, refused: tuple[Quarantined, ...], *, dropped: Iterable[str] = ()) -> Mount:
        """This mount recording ``refused``, without the ``dropped`` plugins it composed."""
        names = set(dropped)
        declaration = self.declaration.model_copy(
            update={
                "plugins": tuple(p for p in self.declaration.plugins if p.name not in names),
                "kinds": tuple(k for k in self.declaration.kinds if k.plugin not in names),
                "quarantined": refused,
            }
        )
        return replace(self, declaration=declaration)

    def command(self, decl: CommandDecl) -> type[Command[Any]]:
        """The class ``decl`` was read from, importing its plugin's class.

        A generated command (``derive``) shares its target with every other one
        of its name, so the words, never the target, say which class runs.
        """
        name = decl.path[0]
        if name not in self._found:
            self._found[name] = self._commands(name)
        return self._found[name][decl.path]

    def _commands(self, name: str) -> Mapping[tuple[str, ...], type[Command[Any]]]:
        """Plugin ``name``'s commands by their words: its declaration walked beside its class."""
        found: dict[tuple[str, ...], type[Command[Any]]] = {}

        def walk(decls: Sequence[CommandDecl | GroupDecl], nodes: Sequence[object]) -> None:
            # A composed plugin had no problem, so every node has its declaration.
            for decl, node in zip(decls, nodes, strict=True):
                if isinstance(decl, GroupDecl) and _is_group(node):
                    if decl.runs is not None and _is_command(node.runs):
                        found[decl.runs.path] = node.runs
                    walk(decl.commands, node.commands)
                elif isinstance(decl, CommandDecl) and _is_command(node):
                    found[decl.path] = node

        plugin, cls = self.declaration.plugin(name), self.plugins[name]
        walk(plugin.commands, cls.commands)
        if plugin.runs is not None and _is_command(cls.runs):
            found[plugin.runs.path] = cls.runs
        return found

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


class _Loaded[V](Mapping[str, V]):
    """A value per plugin name, computed by ``load`` on first read and kept."""

    def __init__(self, names: Sequence[str], load: Callable[[str], V], known: dict[str, V]) -> None:
        self._names = tuple(names)
        self._load = load
        self._known = known

    def __getitem__(self, name: str) -> V:
        if name not in self._known:
            if name not in self._names:
                raise KeyError(name)
            self._known[name] = self._load(name)
        return self._known[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


def _plugin_class(declaration: Declaration, name: str) -> type[Plugin]:
    """Plugin ``name``'s class, imported from its entry point's target."""
    target = declaration.plugin(name).target
    cls = resolve_target(target)
    if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise TypeError(f"{target} is no longer a plugin class; run `untaped doctor`")
    return cast("type[Plugin]", cls)


def _settings_model(
    declaration: Declaration, plugins: Mapping[str, type[Plugin]], name: str
) -> type[BaseModel]:
    """Plugin ``name``'s settings model: its own module only, else through the plugin class.

    A model the class reaches but no module names (one defined in a function)
    is read from the class, which imports it.
    """
    try:
        model = resolve_target(declaration.plugin(name).settings)
    except Exception:
        model = None
    return model if _is_model(model) else plugins[name].settings


def _is_group(obj: object) -> TypeGuard[type[CommandGroup]]:
    return isinstance(obj, type) and issubclass(obj, CommandGroup)


def _is_command(obj: object) -> TypeGuard[type[Command[Any]]]:
    return isinstance(obj, type) and issubclass(obj, Command)


def _is_model(obj: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


def compose_plugins(entries: Sequence[PluginEntry], *, taken: Iterable[str] = ()) -> Mount:
    """Compose the plugin classes of ``entries`` (each a :class:`Plugin` subclass) in one walk.

    ``taken`` are the names no plugin may have (:func:`untaped.runtime.composition.compose`).
    """
    from untaped.runtime.composition import compose  # noqa: PLC0415 - a start reads the cache

    declaration = compose(entries, taken=taken)
    by_name = {entry.name: cast("type[Plugin]", entry.plugin) for entry in entries}
    return Mount.of(
        declaration, {plugin.name: by_name[plugin.name] for plugin in declaration.plugins}
    )


def plugin_spec(
    mount: Mount, name: str, packages: Callable[[], Mapping[str, str]] | None = None
) -> PluginSpec:
    """The composed plugin ``name`` as the :class:`PluginSpec` the rest of composition reads.

    It is built from the declaration alone, so composing never imports the
    plugin class: its help is the plugin's summary (its commands are mounted
    from the declaration the first time one is dispatched), its settings
    model comes from its own module, and its owned blocks are counted from the
    declaration and read from the class the first time a reader asks for them
    (:class:`~untaped.runtime.registry.DeclaredBlocks`). ``packages`` gives
    the 11.x owners by top-level package, for its fills.
    """
    plugin = mount.declaration.plugin(name)
    # a root plugin's commands mount at the root from the declaration, no app built for them
    has_commands = not plugin.root and (bool(plugin.commands) or plugin.runs is not None)
    # core's stub skill is named after it, a plugin's after the plugin under core's name
    skill_name = name if plugin.root else f"untaped-{name}"
    skills = (SkillAsset(name=skill_name, description=plugin.skill),) if plugin.skill else ()
    loaded = functools.cache(partial(_spelled_blocks, mount, name))
    # none declared: nothing to import the class for (as DeclaredBlocks)
    behaviours = any(block.block == "deprecated-behaviour" for block in plugin.blocks)
    factory = partial(build_app, mount, name)
    # the factory builds the plugin's app, so it stands for the plugin's module
    # where its package is looked for (untaped.runtime.plugin_package)
    factory.__module__ = plugin.target.partition(":")[0]
    spec = PluginSpec(
        name=name,
        app_factory=factory if has_commands else None,
        settings=mount.settings[name],
        help=plugin.summary if has_commands else None,
        stability=_mark(plugin.stability),
        skills=skills,
        deprecations=lambda: loaded()["deprecations"] if behaviours else (),
        **_contract_fields(mount, plugin, packages or dict),
    )
    for field_name in ("caches", "doctor_checks", "credentials", "stores"):
        kinds = {kind for kind, each in _SPEC_FIELDS.items() if each == field_name}
        declared = [
            block
            for block in plugin.blocks
            if block.block in kinds and (block.block != "dir" or block.layout or block.items)
        ]
        blocks = DeclaredBlocks(
            tuple(f"{name}.{block.name}" for block in declared),
            partial(lambda field: loaded()[field], field_name),
            timed=_timed_caches(name, declared) if field_name == "caches" else (),
        )
        # the spec's own checks read every block, so the lazy ones go in after them
        object.__setattr__(spec, field_name, blocks)
    return spec


def _contract_fields(
    mount: Mount, plugin: PluginDecl, packages: Callable[[], Mapping[str, str]]
) -> dict[str, Any]:
    """The 11.x registry's ``contracts`` and ``provides`` from ``offers`` and ``fills``.

    Both import the classes only when the registry first asks, as the 11.x
    functions did. A fill's owner is the composed plugin class offering its
    contract, else the 11.x plugin whose package (in ``packages``) defines
    it; a fill whose contract neither owns is left out, since a class names
    no plugin before its owner is composed. The seam goes with the last 11.x
    owner or provider; then an owner not composed becomes a composition row.
    """
    fields: dict[str, Any] = {}
    if plugin.offers:
        fields["contracts"] = partial(_resolved, plugin.offers)
    owners = {target: each.name for each in mount.declaration.plugins for target in each.offers}
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, fill in enumerate(plugin.fills):
        package = fill.contract.partition(":")[0].partition(".")[0]
        owner = owners.get(fill.contract) or packages().get(package)
        if owner is not None:
            grouped[owner].append(index)
    if grouped:
        fields["provides"] = {
            owner: partial(_instances, mount, plugin.name, tuple(indexes))
            for owner, indexes in grouped.items()
        }
    return fields


def _resolved(targets: Sequence[str]) -> tuple[Any, ...]:
    return tuple(resolve_target(target) for target in targets)


def _instances(mount: Mount, name: str, indexes: Sequence[int]) -> tuple[Any, ...]:
    """One instance of each provider class: core instantiates a provider at the first ask.

    The classes are the plugin class's ``fills``, in the declaration's order,
    so a class made at run time (``providing``) needs no importable name and
    two such classes sharing one never stand for each other.
    """
    filled = mount.plugins[name].fills
    return tuple(filled[index]() for index in indexes)


def _timed_caches(plugin: str, declared: Sequence[BlockDecl]) -> tuple[Cache[Any], ...]:
    """Each declared cache with a max age, as its setting reads it: label, max age, old keys."""
    return tuple(
        Cache(
            plugin,
            block.name,
            label=block.cache.label,
            max_age=timedelta(seconds=block.cache.max_age_seconds),
            renamed_from=block.cache.renamed_from,
            removed_in=block.cache.removed_in,
        )
        for block in declared
        if block.cache is not None and block.cache.max_age_seconds is not None
    )


#: The ``PluginSpec`` field each owned block kind goes to; ``None`` for a kind
#: no 11.x reader may see (a plugin class's state rows live in its own file,
#: not the shared one ``PluginSpec.stores`` describes) or needs (a lock). A
#: ``Dir`` goes to ``stores`` when it is numbered (its layouts, which references
#: name and doctor lists) or holds ``items=`` (its item store, which holds use).
_SPEC_FIELDS: dict[str, str | None] = {
    "cache": "caches",
    "doctor-check": "doctor_checks",
    "credential": "credentials",
    "deprecated-behaviour": "deprecations",
    "state-rows": None,
    "lock": None,
    "dir": "stores",
}


def _spelled_blocks(mount: Mount, name: str) -> dict[str, tuple[Any, ...]]:
    """The plugin's owned blocks by spec field, each given its plugin and name for 11.x readers."""
    cls = mount.plugins[name]
    blocks: dict[str, list[Any]] = {
        field: [] for field in _SPEC_FIELDS.values() if field is not None
    }
    held = held_by(cls)
    for decl in mount.declaration.plugin(name).blocks:
        block = held[decl.attribute]
        # every owned block core ships has an entry (tested); one of a kind core
        # doesn't know has no 11.x reader to reach
        field = _SPEC_FIELDS.get(decl.block)
        if isinstance(block, Dir):
            spelled = block._spelled_as(name, decl.name)
            if block.layout is not None or block.item_type is not None:
                blocks["stores"].append(
                    spelled._layouts() if block.layout is not None else spelled.items()
                )
        elif isinstance(block, OwnedBlock) and field is not None:
            blocks[field].append(block._spelled_as(name, decl.name))
    return {field: tuple(each) for field, each in blocks.items()}


# --- the command line ----------------------------------------------------------


def build_app(mount: Mount, name: str) -> App:
    """The Cyclopts app of plugin ``name``: its groups and commands, from the declaration."""
    plugin = mount.declaration.plugin(name)
    app = App(name=name, help=plugin.doc)
    add_commands(app, mount, name)
    if plugin.runs is not None:
        app.default(_command(mount, plugin.runs))
    return app


def add_commands(app: App, mount: Mount, name: str) -> None:
    """Add plugin ``name``'s groups and commands to ``app``: its own, or the root for core's."""
    _add(app, mount, mount.declaration.plugin(name).commands)


def _add(app: App, mount: Mount, nodes: Sequence[CommandDecl | GroupDecl]) -> None:
    for node in nodes:
        if isinstance(node, GroupDecl):
            sub = App(name=node.name, help=node.doc)
            mark = _mark(node.stability)
            if mark is not None:
                mark_app(sub, mark, source="own")
            _add(sub, mount, node.commands)
            if node.runs is not None:
                sub.default(_command(mount, node.runs))
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
    setattr(command, _DECLARED_ATTR, decl)
    mark = _mark(decl.stability)
    return mark(command) if mark is not None else command


_DECLARED_ATTR = "__untaped_declared__"


def declared(func: object) -> CommandDecl | None:
    """The declaration the mount made ``func`` from; ``None`` for any other command."""
    found = getattr(func, _DECLARED_ATTR, None)
    return found if isinstance(found, CommandDecl) else None


def repairs_at(app: App) -> bool | None:
    """Whether the command ``app`` parses to repairs the setup (``repairs = True``).

    A group without a command of its own (its help, a bad flag) repairs when
    every command in it does. ``None`` when ``app`` is no declared command or
    group: one mounted through the 11.x seam.
    """
    decl = declared(app.default_command)
    if decl is not None:
        return decl.repairs
    marks: list[bool] = []
    for name in app:
        if name.startswith("-"):
            continue
        mark = repairs_at(app[name])
        if mark is None:
            return None
        marks.append(mark)
    return all(marks) if marks else None


def is_declared(func: object) -> bool:
    """Whether ``func`` is a command the mount made from a plugin class's declaration."""
    return declared(func) is not None


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
            # one value per flag, as `--case a --case b`, so a positional after it stays one
            settings["consume_multiple"] = False
        mark = _mark(option.stability)
        if isinstance(mark, Deprecated):
            settings["group"] = DEPRECATED_OPTIONS
            metadata.append(mark)
    if option.flag == "--format":
        settings["show_default"] = format_default
        return Annotated[value, Parameter(**settings), *metadata], option.default or "table"
    if option.required and option.block is None:
        return Annotated[value, Parameter(**settings), *metadata], inspect.Parameter.empty
    shown = option.default is not None and option.default is not False
    shown = shown and option.default not in ([], "")
    settings["show_default"] = DeclaredDefault(option.default) if shown else False
    return Annotated[value | None, Parameter(**settings), *metadata], None


@dataclass(frozen=True, eq=False)  # hashable whatever the default holds
class DeclaredDefault:
    """An option's declared default, as help shows it; the parser's default stays ``None``.

    The flag's value is ``None`` when not given, so the filler tells a given
    flag from its default; the surface reader reads the default from here.
    """

    value: object

    def __call__(self, _value: object) -> str:
        if isinstance(self.value, list):
            return ", ".join(str(each) for each in self.value)
        return str(self.value)


def _mark(stability: StabilityDecl | None) -> Stability | None:
    if stability is None:
        return None
    if stability.stability == "experimental":
        return experimental
    return deprecated(removed_in=stability.removed_in, replacement=stability.replacement)


# --- dispatch ------------------------------------------------------------------


def _dispatch(mount: Mount, decl: CommandDecl, given: Sequence[tuple[OptionDecl, object]]) -> None:
    """Build the command from the tokens given, run it under the run, print what it returns."""
    from untaped.runtime.run import current_or_built  # noqa: PLC0415 - a start runs no command

    with report_errors():
        cls = mount.command(decl)
        with current_or_built(mount.declaration, mount.settings) as run:
            command = _instance(mount, decl, cls, given)
            dispatched_now = _DISPATCHED.get()
            if dispatched_now is not None:
                dispatched_now.append(Dispatched(decl, run, dry_run=_dry_run(decl, command)))
            _ran(mount, decl, command)


@dataclass(frozen=True)
class Dispatched:
    """A declared command the parser dispatched: what core's checks after it read."""

    decl: CommandDecl
    run: Run
    #: ``--dry-run`` was passed to its ``Confirm`` or ``DryRun``.
    dry_run: bool

    @property
    def preview(self) -> bool:
        """It only previewed: ``--dry-run``, or a ``Confirm.as_dry_run()`` during ``run``."""
        return self.dry_run or self.run.notes.preview


_DISPATCHED: ContextVar[list[Dispatched] | None] = ContextVar(
    "untaped_mount_dispatched", default=None
)


@contextmanager
def dispatching() -> Iterator[list[Dispatched]]:
    """Record each declared command dispatched in the block, before it runs."""
    seen: list[Dispatched] = []
    token = _DISPATCHED.set(seen)
    try:
        yield seen
    finally:
        _DISPATCHED.reset(token)


def _dry_run(decl: CommandDecl, command: Command[Any]) -> bool:
    """Whether ``command``'s ``Confirm`` or ``DryRun`` was given ``--dry-run``."""
    for option in decl.options:
        if option.block is None:
            continue
        block = _at(command, option.field)
        if (isinstance(block, Confirm) and block.dry_run) or (isinstance(block, DryRun) and block):
            return True
    return False


def _at(command: Command[Any], path: str) -> object:
    """The value at ``path`` (``controls.confirm``); ``None`` past a bundle nobody built."""
    value: object = command
    for part in path.split("."):
        value = getattr(value, part, None)
    return value


@dataclass
class Printed:
    """What one dispatched command printed: its rows as objects, or its line."""

    decl: CommandDecl
    #: The format its ``Output`` printed in; ``None`` for a command without one.
    format: str | None
    rows: list[BaseModel] = field(default_factory=list)
    text: str | None = None


_WATCHING: ContextVar[list[Printed] | None] = ContextVar("untaped_mount_watching", default=None)


@contextmanager
def watching() -> Iterator[list[Printed]]:
    """Record what each command dispatched in the block printed (a test's ``.cli``)."""
    seen: list[Printed] = []
    token = _WATCHING.set(seen)
    try:
        yield seen
    finally:
        _WATCHING.reset(token)


def run_built(
    mount: Mount,
    decl: CommandDecl,
    command: Command[Any],
    flags: Mapping[str, Mapping[str, object]],
) -> Printed:
    """Run a command built outside the parser, its core fields filled from ``flags``, and print.

    ``flags`` gives each core field's flags by field path (``{"output":
    {"--format": "json"}}``); an optional bundle holding a core field is
    built around it, as the parser builds it. Errors propagate: the
    caller reports them. It runs under the current run, or one built for it.
    """
    from untaped.runtime.run import current_or_built  # noqa: PLC0415 - a start runs no command

    with current_or_built(mount.declaration, mount.settings):
        return _ran(mount, decl, _refilled(mount, decl, command, flags))


def _ran(mount: Mount, decl: CommandDecl, command: Command[Any]) -> Printed:
    """Run ``command`` under the current run, print what it returns, and say what it printed."""
    printed = Printed(decl=decl, format=_format_of(decl, command))
    watching_now = _WATCHING.get()
    if watching_now is not None:
        watching_now.append(printed)
    _print(mount, decl, command, command.run(), printed)
    return printed


def _format_of(decl: CommandDecl, command: Command[Any]) -> str | None:
    if not any(option.block == "output" for option in decl.options):
        return None
    return _output(decl, command).format


def _refilled(
    mount: Mount,
    decl: CommandDecl,
    command: Command[Any],
    flags: Mapping[str, Mapping[str, object]],
) -> Command[Any]:
    """``command`` with each core field at a path in ``flags`` filled from its flags.

    An optional bundle nobody built is built around its core field, as the
    parser builds it; one that needs more is a usage error.
    """
    tree: dict[str, Any] = {}
    for path, given in flags.items():
        *bundles, last = path.split(".")
        node = tree
        for part in bundles:
            node = node.setdefault(part, {})
        node[last] = _Given(given)
    return cast("Command[Any]", _refill(mount, decl, command, tree))


@dataclass(frozen=True)
class _Given:
    """The flags given to one core field, a leaf of ``_refilled``'s tree."""

    flags: Mapping[str, object]


def _refill(
    mount: Mount, decl: CommandDecl, holder: BaseModel | type[BaseModel], tree: dict[str, Any]
) -> BaseModel:
    """``holder`` with each core field in ``tree`` filled; a bundle is built once from them all."""
    cls = holder if isinstance(holder, type) else type(holder)
    values: dict[str, object] = {}
    for part, node in tree.items():
        info = cls.model_fields[part]
        current = getattr(holder, part) if isinstance(holder, BaseModel) else info.default
        if isinstance(node, _Given):
            values[part] = _filled(mount, decl, info, node.flags, current)
        elif isinstance(current, BaseModel):
            values[part] = _refill(mount, decl, current, node)
        else:
            values[part] = _refill(mount, decl, _bundle_model(info.annotation), node)
    if isinstance(holder, BaseModel):
        return holder.model_copy(update=values)
    try:
        return holder.model_validate({_keys(holder, part)[0]: v for part, v in values.items()})
    except ValidationError as exc:
        # the bundle's own fields are no flags of this command
        raise UsageError(first_validation_error(exc)) from exc


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
        _put(data, cls, path, _filled(mount, decl, info, flags, info.default))
    # outermost first: a required bundle inside an optional one nobody used stays unbuilt
    for path in _bundles(decl):
        parent = path.rpartition(".")[0]
        if parent and _get(data, cls, parent) is None:
            continue
        if _get(data, cls, path) is None and _field(cls, path).is_required():
            _put(data, cls, path, {})
    try:
        command = cls.model_validate(data)
    except ValidationError as exc:
        raise UsageError(_usage(decl, cls, exc)) from exc
    _check_files(decl, command)
    return command


def _check_files(decl: CommandDecl, command: BaseModel) -> None:
    """Refuse a ``File[M]`` the command reads whose path names nothing (exit 2)."""
    for option in decl.options:
        if not option.reads_file:
            continue
        value: object = command
        for part in option.field.split("."):
            value = getattr(value, part, None)
        files = value if isinstance(value, list | tuple) else [value]
        for each in files:
            if isinstance(each, File) and not each.path.exists():
                raise UsageError(f"{option.flag or option.metavar}: file not found: {each.path}")


def _filled(
    mount: Mount,
    decl: CommandDecl,
    info: FieldInfo,
    flags: Mapping[str, object],
    current: object,
) -> FieldBlock:
    """The core field ``info`` declares, filled by itself from its flags for this invocation.

    ``current`` is the field's value before (its default, or a built
    command's): it configures the block.
    """
    block = field_block(info.annotation)
    assert block is not None  # the declaration's block options come from core fields
    default = current if isinstance(current, block) else block()
    call = Invocation(
        words=("untaped", *mount.declaration.words(decl)),
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
    """The data keys of the field at ``path``: each step's validation alias or alias, if any."""
    keys: list[str] = []
    parts = path.split(".")
    for end in range(len(parts)):
        model, name = _owner(cls, ".".join(parts[: end + 1]))
        info = model.model_fields[name]
        alias = info.validation_alias if isinstance(info.validation_alias, str) else info.alias
        keys.append(alias or name)
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


def _usage(decl: CommandDecl, cls: type[BaseModel], exc: ValidationError) -> str:
    """A validation error as the flag or argument it is about, and what is wrong."""
    error = exc.errors()[0]
    loc = [str(part) for part in error.get("loc", ()) if not isinstance(part, int)]
    option = next((o for o in decl.options if o.block is None and _keys(cls, o.field) == loc), None)
    if option is None:
        return first_validation_error(exc)
    message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{option.flag or option.metavar}: {message}"


# --- printing --------------------------------------------------------------------


def _print(
    mount: Mount, decl: CommandDecl, command: Command[Any], result: object, printed: Printed
) -> None:
    """Print what ``run`` returned, as its return type declares, keeping it in ``printed``."""
    if decl.returns == "nothing" or result is None:
        return
    if decl.returns in ("line", "line-or-none"):
        printed.text = str(result)
        print(result)
        return
    if isinstance(result, BaseModel):
        printed.rows.append(result)
        _print_rows(mount, decl, command, result)
        return
    rows = printed.rows
    try:
        rows.extend(cast("Iterable[BaseModel]", result))
    except UntapedError as exc:  # ``raise Failed()`` after the rows: they are printed first
        if rows or isinstance(exc, Failed | Matched):  # ``[]`` in JSON: the run finished
            _print_rows(mount, decl, command, rows)
        raise
    _print_rows(mount, decl, command, rows)


def _print_rows(
    mount: Mount, decl: CommandDecl, command: Command[Any], records: BaseModel | list[BaseModel]
) -> None:
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


__all__ = [
    "Dispatched",
    "Mount",
    "Printed",
    "add_commands",
    "build_app",
    "compose_plugins",
    "declared",
    "dispatching",
    "is_declared",
    "plugin_spec",
    "repairs_at",
    "run_built",
    "watching",
]
