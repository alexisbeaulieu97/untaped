"""Composition's walk of one command class: its fields, flags, blocks and what it returns.

:class:`CommandWalk` reads a command the way the plugin walk
(:mod:`untaped.runtime.plugin_walk`) reaches it, and records each problem
the shape cannot prevent on that plugin's walk.
"""

from __future__ import annotations

import collections.abc
import enum
import types
import typing
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from untaped.runtime.declaration import (
    AliasDecl,
    CommandDecl,
    OptionDecl,
    Returns,
    StabilityDecl,
    target_of,
)
from untaped.runtime.describe import (
    jsonable,
    prose,
)
from untaped.runtime.root_options import ROOT_FLAGS
from untaped.sdk._block import OR_STDIN, POSITIONAL, FieldBlock
from untaped.sdk._input_blocks import Stdin
from untaped.sdk._keys import SettingKey
from untaped.sdk._names import _WORDS
from untaped.sdk._output import Output
from untaped.sdk._run_blocks import CORE_SHORTS, Parallel, Refresh
from untaped.sdk._table import unprintable_following
from untaped.sdk._value_types import File
from untaped.sdk.fields import AllowHyphen, Document, Short, Written
from untaped.sdk.marks import (
    Deprecated,
    Experimental,
)
from untaped.sdk.plugin import (
    Command,
    field_block,
    generic_argument,
    settings_model,
)
from untaped.sdk.storage.cache import Cache, _max_age_setting, _refresh_help

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

    from untaped.runtime.plugin_walk import PluginWalk


#: Field names core or pydantic reserves on a command; ``run_`` still spells ``--run``.
RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "run",
        "ctx",
        "settings",
        "spelled",
        "stability",
        "aliases",
        "repairs",
        "json",
        "copy",
        "schema",
        "validate",
        "dict",
        "construct",
    }
)


_ITERATORS = frozenset(
    {
        collections.abc.Iterator,
        collections.abc.Iterable,
        collections.abc.Generator,
        typing.Iterator,
        typing.Iterable,
        typing.Generator,
    }
)


def bundle_panel(model: type) -> str:
    """A bundle's help panel: its class name as words, ``SourceData`` is ``Source data``."""
    first, *rest = _WORDS.findall(model.__name__)
    return " ".join([first, *(word if word.isupper() else word.lower() for word in rest)])


def mark_stability(mark: object) -> StabilityDecl | None:
    if isinstance(mark, Experimental):
        return StabilityDecl(stability="experimental")
    if isinstance(mark, Deprecated):
        replacement = mark.replacement
        if replacement is None or isinstance(replacement, str | SettingKey):
            text = None if replacement is None else str(replacement)
            return StabilityDecl(
                stability="deprecated", removed_in=mark.removed_in, replacement=text
            )
        # a command class: spelled as its line once the plugin's commands are walked
        return StabilityDecl(
            stability="deprecated", removed_in=mark.removed_in, replaced_by=target_of(replacement)
        )
    return None


def is_deprecated(mark: StabilityDecl | None) -> bool:
    return mark is not None and mark.stability == "deprecated"


def is_model(obj: object) -> typing.TypeGuard[type[BaseModel]]:
    return isinstance(obj, type) and issubclass(obj, BaseModel)


@dataclass
class _Seen:
    """What a command's fields add up to while they are walked."""

    options: list[OptionDecl] = field(default_factory=list)
    blocks: Counter[str] = field(default_factory=Counter)
    guides: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    stdin: list[str] = field(default_factory=list)
    stdin_via: str | None = None
    stdin_via_bridge: str | None = None
    output: type[BaseModel] | None = None
    formats: tuple[str, ...] = ()
    streams: bool = False


class CommandWalk:
    """One command's walk: its options, what it reads and prints, and its problems."""

    def __init__(self, plugin: PluginWalk, cls: type[Command[Any]], path: tuple[str, ...]) -> None:
        self.plugin = plugin
        self.cls = cls
        self.path = path
        self.where = cls.__qualname__
        self.seen = _Seen()
        #: How many optional bundles the walk is inside: their fields are never required.
        self.optional_bundles = 0

    def problem(self, rule: str, where: str, detail: str) -> None:
        self.plugin.problem(rule, where, detail)

    def decl(self) -> CommandDecl | None:
        cls = self.cls
        if not cls.__pydantic_complete__:
            try:
                cls.model_rebuild(raise_errors=True)
            except Exception as exc:
                self.problem(
                    "unresolved-type", self.where, f"a field type is not defined at run time: {exc}"
                )
                return None
        self._settings()
        returns = self._returns()
        for name, info in cls.model_fields.items():
            self._field(name, info, prefix="", panel="Options")
        self._duplicates()
        self._positionals()
        self._output(returns)
        self._removes()
        summary, doc = prose(cls)
        if not summary:
            self.problem("missing-docstring", self.where, "a command's docstring is its help")
        if returns is None:
            return None
        seen = self.seen
        return CommandDecl(
            name=self.path[-1],
            path=self.path,
            target=target_of(cls),
            summary=summary,
            doc=doc,
            settings=self.plugin.name if settings_model(cls) is not None else None,
            options=tuple(seen.options),
            returns=returns[0],
            output=None if seen.output is None else self._kind(seen.output),
            formats=seen.formats,
            streams=seen.streams,
            stdin=tuple(seen.stdin),
            stdin_via=seen.stdin_via,
            stdin_via_bridge=seen.stdin_via_bridge,
            inputs=tuple(seen.inputs),
            writes=tuple(seen.writes),
            guides=tuple(dict.fromkeys(seen.guides)),
            asks_first=seen.blocks["confirm"] > 0,
            stability=self._mark(cls),
            aliases=self._aliases(),
            repairs=self._repairs(),
        )

    def _removes(self) -> None:
        """Record the state rows the command marks ``removes =``: rows its plugin class holds.

        Keyed rows' remover takes the key under the key field's name, as its first
        positional or as an option, so a damaged row's hint can spell the command.
        """
        rows = self.cls.removes
        if rows is None:
            return
        attribute = self.plugin.held.get(id(rows))
        if attribute is None or getattr(rows, "block_kind", None) != "state-rows":
            self.problem(
                "reserved-name",
                f"{self.where}.removes",
                f"holds {rows!r}, not state rows its plugin class holds",
            )
            return
        removers = self.plugin.removers.setdefault(attribute, {})
        key = rows._keyed_by()
        if key is None:  # rows that are a list, or a shape the block's own problems name
            removers[target_of(self.cls)] = (self.where, None)
            return
        top = [o for o in self.seen.options if o.block is None and "." not in o.field]
        option = next((o for o in top if o.field.rstrip("_") == key), None)
        first = next((o for o in top if o.positional), None)
        if option is None or (option.positional and option is not first):
            self.problem(
                "remover-key",
                f"{self.where}.removes",
                f"removes {self.plugin.name}.{attribute}, whose rows are keyed by {key!r}, "
                f"but takes no top-level field {key!r} as its first positional or an option: "
                f"add one taking the row's key",
            )
            return
        removers[target_of(self.cls)] = (self.where, option.flag)

    def _mark(self, cls: type[Command[Any]]) -> StabilityDecl | None:
        return self.plugin.stability_of(cls, cls.stability)

    def _repairs(self) -> bool:
        repairs: object = self.cls.repairs
        if not isinstance(repairs, bool):
            self.problem("reserved-name", f"{self.where}.repairs", f"holds {repairs!r}, not a bool")
            return False
        return repairs

    def _aliases(self) -> tuple[AliasDecl, ...]:
        aliases = self.cls.aliases
        if not isinstance(aliases, tuple) or not all(
            isinstance(alias, Deprecated) and isinstance(alias.replacement, str)
            for alias in aliases
        ):
            self.problem(
                "reserved-name",
                f"{self.where}.aliases",
                f"holds {aliases!r}, not a tuple of "
                'Deprecated(replacement="old-name", removed_in=...)',
            )
            return ()
        return tuple(
            AliasDecl(name=str(alias.replacement), removed_in=alias.removed_in) for alias in aliases
        )

    def _kind(self, model: type[BaseModel]) -> str:
        return self.plugin.kind(model)

    def _settings(self) -> None:
        model = settings_model(self.cls)
        if model is not None and model is not getattr(self.plugin, "settings", None):
            self.problem(
                "settings-model",
                self.where,
                f"is Command[{model.__name__}], but the plugin's settings model is "
                f"{getattr(self.plugin, 'settings', type(None)).__name__}",
            )

    # --- what run returns ----------------------------------------------------------

    def _returns(self) -> tuple[Returns, type[BaseModel] | None] | None:
        run = self.cls.run
        if run is Command.run:
            self.problem("missing-run", self.where, "defines no run()")
            return None
        try:
            hints = get_type_hints(run)
        except Exception as exc:
            self.problem(
                "unresolved-type",
                f"{self.where}.run",
                f"the return type is not defined at run time: {exc}",
            )
            return None
        if "return" not in hints:
            self.problem(
                "missing-return",
                f"{self.where}.run",
                "needs a return annotation: it says what the command prints",
            )
            return None
        shape = _shape(hints["return"])
        if shape is None:
            self.problem(
                "output-return",
                f"{self.where}.run",
                f"returns {hints['return']!r}; core prints Iterator[R], R, R | None, "
                "R | Iterator[R], str, str | None or None",
            )
        return shape

    def _output(self, returns: tuple[Returns, type[BaseModel] | None] | None) -> None:
        if returns is None:
            return
        shape, model = returns
        output = self.seen.output
        if model is not None and output is None:
            self.problem(
                "output-return",
                self.where,
                f"run returns {model.__name__} records but declares no Output[R] field",
            )
        elif output is not None and model is None:
            self.problem(
                "output-return",
                self.where,
                f"declares Output[{output.__name__}] but run returns {shape}, not records",
            )
        elif output is not None and model is not None and not issubclass(output, model):
            self.problem(
                "output-return",
                self.where,
                f"declares Output[{output.__name__}] but run returns {model.__name__}, "
                "which is not it or a base of it",
            )
        if self.seen.streams and shape != "rows":
            self.problem(
                "output-return",
                self.where,
                f"declares Output(stream=True) but run returns {shape}: a stream yields rows "
                "(Iterator[R])",
            )
        for name in unprintable_following(output) if output is not None else ():
            self.problem(
                "field-type",
                f"{self.where}.output",
                f"{target_of(output)}.{name} is marked Following but is not a list of models: "
                "a table prints a Following field as its own table of the items",
            )

    # --- fields ------------------------------------------------------------------

    def _field(self, name: str, info: FieldInfo, *, prefix: str, panel: str) -> None:
        path = f"{prefix}{name}"
        where = f"{self.where}.{path}"
        if name in RESERVED_FIELDS:
            self.problem(
                "reserved-field",
                where,
                f"core or pydantic reserves `{name}`; name the field `{name}_` and it "
                f"still spells --{name}",
            )
            return
        annotation = info.annotation
        block = field_block(annotation)
        if block is not None:
            self._core(path, where, block, info)
            return
        if _nested_block(annotation):
            self.problem(
                "field-type",
                where,
                "a core-filled field type is the whole field type, never inside another",
            )
            return
        value, repeated, inner = _unwrap(annotation)
        marks = (*info.metadata, *inner)
        if is_model(value) and POSITIONAL not in marks and not repeated:
            self._bundle(path, value, required=info.is_required())
            return
        self._option(path, where, info, value=value, marks=marks, repeated=repeated, panel=panel)

    def _bundle(self, path: str, model: type[BaseModel], *, required: bool) -> None:
        guide = bool(prose(model)[0])
        if guide:
            self.seen.guides.append(target_of(model))
        start = len(self.seen.options)
        self.optional_bundles += not required
        for name, info in model.model_fields.items():
            self._field(name, info, prefix=f"{path}.", panel=bundle_panel(model))
        self.optional_bundles -= not required
        if guide:
            self.plugin.guide(model, options=tuple(self.seen.options[start:]))

    def _option(
        self,
        path: str,
        where: str,
        info: FieldInfo,
        *,
        value: object,
        marks: tuple[object, ...],
        repeated: bool,
        panel: str,
    ) -> None:
        token = self._token(value, where)
        if token is None:
            return
        name = path.rpartition(".")[2].rstrip("_")
        positional = POSITIONAL in marks
        if not info.description:
            self.problem(
                "missing-description",
                where,
                "every option and argument has help: Field(description=...)",
            )
        shorts = [mark.flag for mark in marks if isinstance(mark, Short)]
        for short in shorts:
            if short in CORE_SHORTS:
                self.problem("core-short", where, f"{short} belongs to core's own flags")
        default = None if info.default is PydanticUndefined else info.default
        negative = None
        # a flag on by default turns off with --no-X; a ``bool | None`` (unset
        # unless given) takes either, so --no-X says false outright
        if token[0] == "bool" and not positional and (default is True or info.default is None):
            negative = f"--no-{name.replace('_', '-')}"
        flag = None if positional else f"--{name.replace('_', '-')}"
        for spelled in (flag, negative, *shorts):
            if spelled in ROOT_FLAGS:
                fix = "give it another Short" if spelled in shorts else "rename the field"
                self.problem(
                    "root-option",
                    where,
                    f"{spelled} is a root option, which every command takes "
                    f"(`untaped {spelled}`); {fix}",
                )
        if OR_STDIN in marks and repeated and value is str:
            self.problem(
                "field-type",
                where,
                "TextOrStdin marks one value, not list items: use list[PathOrStdin]",
            )
        stability = next((s for s in map(mark_stability, marks) if s is not None), None)
        if positional and is_deprecated(stability):
            self.problem(
                "deprecated-argument",
                where,
                "an argument can't be deprecated: make it an option, or deprecate the command",
            )
        self.seen.options.append(
            OptionDecl(
                field=path,
                flag=flag,
                metavar=name.upper(),
                positional=positional,
                token=f"list[{token[0]}]" if repeated else token[0],
                choices=token[1],
                negative=negative,
                short=shorts[0] if shorts else None,
                required=info.is_required() and not self.optional_bundles,
                default=jsonable(default),
                help=info.description or "",
                panel=panel,
                allow_hyphen=AllowHyphen in marks,
                or_stdin=OR_STDIN in marks,
                reads_file=get_origin(value) is File and Written not in marks,
                stability=stability,
            )
        )
        self._file(value, marks)

    def _token(self, value: object, where: str) -> tuple[str, tuple[str, ...]] | None:
        token = _token(value)
        if token is None:
            self.problem(
                "field-type",
                where,
                f"core makes no option of {getattr(value, '__name__', value)!r}: use a plain type, "
                "a Literal, an enum, a pydantic model (a bundle) or one of core's value types",
            )
            return None
        if (
            isinstance(value, type)
            and issubclass(value, enum.Enum)
            and prose(value)[0]
            and not value.__module__.startswith(("untaped.", "enum"))
        ):
            self.seen.guides.append(target_of(value))
            self.plugin.guide(value, choices=token[1])
        return token

    def _file(self, value: object, marks: tuple[object, ...]) -> None:
        for mark in marks:
            if isinstance(mark, Document):
                self.seen.inputs.append(self._kind(mark.model))
        if get_origin(value) is not File:
            return
        model = generic_argument(value)
        if not is_model(model):
            self.problem("field-type", self.where, "File[M] takes a pydantic model M")
            return
        (self.seen.writes if Written in marks else self.seen.inputs).append(self._kind(model))

    def _core(self, path: str, where: str, block: type[FieldBlock], info: FieldInfo) -> None:
        seen = self.seen
        if seen.blocks[block.field_kind]:
            self.problem("duplicate-core-field", where, f"a command has one {block.__name__} field")
            return
        seen.blocks[block.field_kind] += 1
        default = info.default if isinstance(info.default, block) else block()
        refresh_help: str | None = None
        if isinstance(default, Output):
            self._output_field(where, info.annotation, default)
        elif isinstance(default, Stdin):
            self._stdin_field(where, info.annotation, default)
        elif isinstance(default, Refresh):
            refresh_help = self._refresh_field(where, default)
        elif (
            isinstance(default, Parallel)
            and isinstance(default.default, SettingKey)
            and SettingKey.model_of(default.default) is not getattr(self.plugin, "settings", None)
        ):
            self.problem(
                "settings-model", where, "Parallel's default reads another plugin's settings"
            )
        for option in block.options:
            choices: tuple[str, ...] = ()
            if option.flag == "--format" and isinstance(default, Output):
                choices = default.formats
            seen.options.append(
                OptionDecl(
                    field=path,
                    flag=option.flag,
                    metavar=option.flag.removeprefix("--").upper(),
                    token=option.token,
                    choices=choices,
                    negative=option.negative,
                    short=option.short,
                    help=refresh_help
                    or default._option_help(option),  # Refresh's one flag pair, about its caches
                    panel=block.panel,
                    block=block.field_kind,
                    default=default._option_default(option),
                )
            )

    def _output_field(self, where: str, annotation: object, default: Output[Any]) -> None:
        model = generic_argument(annotation)
        if not is_model(model):
            self.problem(
                "field-type", where, "Output[R] takes the pydantic model of the records printed"
            )
            return
        try:
            default._check_declared(model)
        except TypeError as exc:
            self.problem("field-type", where, str(exc))
        self.seen.output = model
        self.seen.formats = default.formats
        self.seen.streams = default.stream
        self._kind(model)

    def _stdin_field(self, where: str, annotation: object, default: Stdin[Any]) -> None:
        accepted = generic_argument(annotation)
        models = [accepted] if is_model(accepted) else list(get_args(accepted))
        if not models or not all(is_model(model) for model in models):
            self.problem(
                "field-type", where, "Stdin[R] takes the pydantic model(s) of the rows accepted"
            )
            return
        self.seen.stdin.extend(target_of(model) for model in models)
        if default.via is not None:
            via = self._via(where, default.via, tuple(models))
            if via is not None:
                self.seen.stdin_via, self.seen.stdin_via_bridge = via

    def _via(
        self, where: str, via: object, models: tuple[type[BaseModel], ...]
    ) -> tuple[str, str] | None:
        """The contract and bridge ``Stdin(via=)`` names, if it names one returning ``R``."""
        from untaped.sdk.contracts._declare import method_contract  # noqa: PLC0415

        try:
            info, method = method_contract(typing.cast("collections.abc.Callable[..., Any]", via))
        except TypeError:
            info = None
        if info is None or not method.bridge:
            self.problem(
                "field-type",
                where,
                f"Stdin(via=) takes a contract's bridge method (BookSource.to_book), not {via!r}",
            )
            return None
        returned = method.hints["return"]
        if not issubclass(returned, models):
            names = " | ".join(model.__name__ for model in models)
            self.problem(
                "field-type",
                where,
                f"Stdin[{names}](via={info.cls.__name__}.{method.name}) reads "
                f"{returned.__name__} rows, which it doesn't accept",
            )
            return None
        return target_of(info.cls), method.name

    def _refresh_field(self, where: str, default: Refresh) -> str | None:
        """Check the caches ``default`` names; the help of its flags, or ``None`` on a problem.

        The help names each cache's label and max-age setting, and goes into
        the declaration, so help never imports the command to say it.
        """
        if not default.caches:
            self.problem(
                "refresh-cache",
                where,
                "Refresh names the caches its flags are about: Refresh(CACHE)",
            )
            return None
        named: list[tuple[Cache[Any], str]] = []
        for each in default.caches:
            if isinstance(each, Cache) and each.version_only:
                self.problem(
                    "refresh-cache",
                    where,
                    f"{each!r} is version-only: it has no max age for --refresh to override",
                )
                continue
            attribute = self.plugin.held.get(id(each))
            if isinstance(each, Cache) and attribute is not None:
                setting = _max_age_setting(self.plugin.name, each.derived_name(attribute))
                named.append((each, setting))
                continue
            cache = self._cached_method(each)
            if cache is not None:
                spelled = self._setting_of(cache, each)
                if spelled is not None:
                    named.append((cache, spelled))
                    continue
                self.problem(
                    "unowned-cache",
                    where,
                    f"{each!r}'s cache {cache!r} belongs to no composed plugin: spell it "
                    "Cache(owner, name, ...) or hold it on the owner's plugin class",
                )
                continue
            self.problem(
                "refresh-cache",
                where,
                f"{each!r} is neither a cache on the plugin class nor a cached method of a "
                "contract the plugin offers or fills",
            )
        return _refresh_help(named) if len(named) == len(default.caches) else None

    def _setting_of(self, cache: Cache[Any], method: object) -> str | None:
        """The max-age setting of a contract method's cache; ``None`` when no plugin owns it.

        A cache bound where declared knows its plugin; one
        ``@cached`` made belongs to the plugin offering the method's contract;
        one a plugin class holds belongs to the plugin composed for that
        class's package, read here because no run exists yet.
        """
        if cache._held_as is not None:
            return cache.setting
        if cache._declared_as is not None and not cache.bindings:
            from untaped.sdk.contracts._declare import method_contract  # noqa: PLC0415

            contract = method_contract(typing.cast("collections.abc.Callable[..., Any]", method))[
                0
            ].cls
            plugin = self.plugin.offering.get(contract)
            return None if plugin is None else _max_age_setting(plugin, cache._declared_as)
        for binding in cache.bindings:
            plugin = self.plugin.owners.get(binding.owner.__module__.partition(".")[0])
            if plugin is not None:
                return _max_age_setting(plugin, cache.derived_name(binding.attribute))
        return None

    def _cached_method(self, method: object) -> Cache[Any] | None:
        """The answer cache of ``method`` if it is a cached method the plugin offers or fills."""
        cache = getattr(method, "__untaped_cached__", None)
        if not isinstance(cache, Cache):
            return None
        name = getattr(method, "__name__", "")
        classes = (*self.plugin.offers, *self.plugin.fills)
        return cache if any(getattr(cls, name, None) is method for cls in classes) else None

    def _duplicates(self) -> None:
        flags: dict[str, str] = {}
        for option in self.seen.options:
            for flag in (option.flag, option.short, option.negative):
                if flag is None:
                    continue
                if flag in flags and flags[flag] != option.field:
                    self.problem(
                        "duplicate-field",
                        f"{self.where}.{option.field}",
                        f"{flag} is also {self.where}.{flags[flag]}'s",
                    )
                flags.setdefault(flag, option.field)

    def _positionals(self) -> None:
        """Arguments fill in field order: none may follow an optional or a list one."""
        before: OptionDecl | None = None
        for option in (each for each in self.seen.options if each.positional):
            if before is not None and before.token.startswith("list["):
                self.problem(
                    "positional-order",
                    f"{self.where}.{option.field}",
                    f"follows {before.field}, a list argument that takes every value left",
                )
            elif before is not None and option.required and not before.required:
                self.problem(
                    "positional-order",
                    f"{self.where}.{option.field}",
                    f"is required but follows {before.field}, an optional argument",
                )
            before = option


# --- types -----------------------------------------------------------------------------


def _unwrap(annotation: object) -> tuple[object, bool, tuple[object, ...]]:
    """``(value type, repeated, marks)`` of a field annotation.

    ``Annotated[list[str], Positional] | None`` is ``(str, True, (POSITIONAL,))``:
    pydantic lifts only top-level ``Annotated`` marks into the field's metadata,
    so the marks inside ``X | None`` or a list are collected here.
    """
    marks: list[object] = []

    def peel(annotation: object) -> object:
        while get_origin(annotation) is typing.Annotated:
            annotation, *found = get_args(annotation)
            marks.extend(found)
        return annotation

    if get_origin(annotation) in (typing.Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            annotation = members[0]
    annotation = peel(annotation)
    repeated = False
    if get_origin(annotation) in (
        list,
        tuple,
        set,
        frozenset,
        collections.abc.Sequence,
    ) and not _is_pair(annotation):
        args = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        if len(args) == 1:
            annotation, repeated = args[0], True
    return peel(annotation), repeated, tuple(marks)


def _is_pair(annotation: object) -> bool:
    """``tuple[str, str]`` and ``tuple[str, JsonValue]``: ``KeyValue`` and ``KeyJson``."""
    args = get_args(annotation)
    return get_origin(annotation) is tuple and len(args) == 2 and Ellipsis not in args


def _token(value: object) -> tuple[str, tuple[str, ...]] | None:
    """The token shape the parser converts ``value`` from, with its choices."""
    simple: dict[object, str] = {bool: "bool", int: "int", float: "float", str: "str"}
    if value in simple:
        return simple[value], ()
    if isinstance(value, type) and issubclass(value, PurePath):
        return "str", ()
    if get_origin(value) is typing.Literal:
        choices = get_args(value)
        token = (
            "int" if all(isinstance(c, int) and not isinstance(c, bool) for c in choices) else "str"
        )
        return token, tuple(str(c) for c in choices)
    if isinstance(value, type) and issubclass(value, enum.Enum):
        return "str", tuple(str(member.value) for member in value)
    if get_origin(value) is File or value is File or _is_pair(value):
        return "str", ()
    return None


def _shape(hint: object) -> tuple[Returns, type[BaseModel] | None] | None:
    """What a return type says the command prints, and the record model it names."""
    shapes: dict[object, Returns] = {None: "nothing", type(None): "nothing", str: "line"}
    if hint in shapes:
        return shapes[hint], None
    if is_model(hint):
        return "record", hint
    origin = get_origin(hint)
    if origin in _ITERATORS:
        model = get_args(hint)[0] if get_args(hint) else None
        return ("rows", model) if is_model(model) else None
    if origin not in (typing.Union, types.UnionType):
        return None
    members = [arg for arg in get_args(hint) if arg is not type(None)]
    has_none = len(members) < len(get_args(hint))
    if has_none and members == [str]:
        return "line-or-none", None
    if has_none and len(members) == 1 and is_model(members[0]):
        return "record-or-none", members[0]
    if not has_none and len(members) == 2:
        records = [m for m in members if is_model(m)]
        rows = [m for m in members if get_origin(m) in _ITERATORS and get_args(m) == tuple(records)]
        if len(records) == 1 and rows:
            return "rows-or-record", records[0]
    return None


def _nested_block(annotation: object) -> bool:
    """Whether a core-filled field type sits inside ``annotation`` (``Output[R] | None``)."""
    return any(
        field_block(arg) is not None or _nested_block(arg)
        for arg in get_args(annotation)
        if not isinstance(arg, str)
    )
