"""The plugin and its commands: ``Plugin``, ``Command``, ``Group``, ``derive`` and ``StubSkill``.

A plugin is a class; what the plugin has are its attributes. A command is a
class; what the command takes are its fields (:mod:`untaped.sdk.fields`).
Names come from where things are (the entry point key, class and attribute
names); prose comes from docstrings. Core reads these classes once, at
composition (``untaped.runtime.composition``), and checks them there, so a
mistake quarantines one plugin with a message naming the class or field
instead of failing an import.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, NoReturn, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model, model_validator

from untaped.sdk._block import Block, FieldBlock
from untaped.sdk.marks import Deprecated, Experimental
from untaped.sdk.run import Context, context_for, settings_of

if TYPE_CHECKING:
    from collections.abc import Iterable

    from untaped.sdk.marks import Stability


class StubSkill(Block):
    """The plugin's stub skill: ``skill = StubSkill("Uses ... Use when ...")``.

    Core names the skill ``untaped-<plugin>`` and has it teach ``untaped docs
    <plugin>``; the description is what an agent reads to decide to load it.
    """

    block_kind = "stub-skill"

    def __init__(self, description: str) -> None:
        super().__init__()
        if not description.strip():
            raise ValueError("StubSkill takes the skill's description, which is empty")
        self.description = description


class SpelledCommand(NamedTuple):
    """A command named by its class, with the arguments a hint shows: ``Set.spelled(key, "URL")``.

    Core spells it (``untaped config set hello.greeting URL``) from the
    composition, so a renamed command or key follows its class.
    """

    command: type[Command[Any]]
    args: tuple[object, ...]


class Command[S: BaseModel](BaseModel):
    """Base of a command: a pydantic model whose fields are what it takes.

    ``S`` is the plugin's settings model (``Command[HelloSettings]``), read in
    ``run`` as ``self.settings``; plain ``Command`` reads none. A field whose
    type core knows (``Output[R]``, ``Stdin[R]``, ``Confirm``, ``Refresh``,
    ``Parallel``, ``Limit``, ``Lines``) is filled by core and adds its flags;
    every other field is an option, or an argument when it is
    ``Positional[...]``, with its ``description`` as help. The docstring is
    the summary and the behaviour; ``run``'s return type says what it prints.
    ``stability`` and ``aliases`` are class-level marks.
    """

    model_config = ConfigDict(extra="forbid", ignored_types=(Experimental, Deprecated))

    stability: ClassVar[Stability | None] = None
    aliases: ClassVar[tuple[Deprecated, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _core_fields(cls, data: Any) -> Any:
        """Give each core-filled field its type's plain instance when none was passed."""
        if not isinstance(data, dict):
            return data
        missing = {
            name: block()
            for name, info in cls.model_fields.items()
            if name not in data
            and info.is_required()
            and (block := field_block(info.annotation)) is not None
        }
        return {**data, **missing} if missing else data

    @property
    def ctx(self) -> Context:
        """What the command reaches without declaring it: ``diag``, ``terminal``,
        ``project`` and ``plugin`` (its own plugin), from the current run."""
        return context_for(type(self))

    @property
    def settings(self) -> S:
        """The plugin's settings (``Command[S]``), as the current run picked them."""
        model = settings_model(type(self))
        if model is None:
            raise TypeError(
                f"{type(self).__name__} reads no settings: declare them as Command[YourSettings]"
            )
        return settings_of(model)  # type: ignore[return-value]  # S is the model read

    def run(self) -> object:
        """The command's body; core calls it after filling the fields."""
        raise NotImplementedError(f"{type(self).__name__} has no run()")

    @classmethod
    def spelled(cls, *args: object) -> SpelledCommand:
        """A reference to this command for a hint or a fix, spelled by core from the composition."""
        return SpelledCommand(cls, args)


class Group:
    """Base of a command group: its docstring is its help and page, ``commands`` its children.

    Its name is the class name in kebab case, a trailing ``Group`` dropped
    (``ProfileGroup`` is ``profile``). ``stability`` marks it as on a plugin.
    """

    commands: ClassVar[tuple[type[Command[Any]] | type[Group], ...]] = ()
    stability: ClassVar[Stability | None] = None

    def __init__(self) -> NoReturn:
        raise TypeError(
            f"{type(self).__name__} is a group; core reads the class, never an instance"
        )


class Plugin:
    """Base of a plugin: a class whose attributes are what the plugin has.

    Seven names are reserved, each one thing: ``settings`` (the settings
    model, the plugin's identity and its config section), ``commands``,
    ``stability``, ``offers`` (contracts the plugin owns), ``fills``
    (providers of other plugins' contracts), ``skill`` (a :class:`StubSkill`)
    and ``runs`` (the command the plugin's root runs). Every other attribute
    holding a core block (a cache, a credential, a doctor check...) is the
    plugin's, named by its attribute. The entry point key is the plugin's
    name: ``hello = "untaped_hello.plugin:Hello"``, in a leaf module.
    """

    settings: ClassVar[type[BaseModel]]
    commands: ClassVar[tuple[type[Command[Any]] | type[Group], ...]] = ()
    stability: ClassVar[Stability | None] = None
    offers: ClassVar[tuple[type, ...]] = ()
    fills: ClassVar[tuple[type, ...]] = ()
    skill: ClassVar[StubSkill | None] = None
    runs: ClassVar[type[Command[Any]] | None] = None

    def __init__(self) -> NoReturn:
        raise TypeError(
            f"{type(self).__name__} is a plugin; core reads the class, never an instance"
        )


#: The attribute names a plugin class reserves, each holding one thing.
RESERVED_NAMES: tuple[str, ...] = (
    "settings",
    "commands",
    "stability",
    "offers",
    "fills",
    "skill",
    "runs",
)


def field_block(annotation: object) -> type[FieldBlock] | None:
    """The core-filled field type ``annotation`` is (``Output[R]`` gives ``Output``), if any."""
    origin = get_origin(annotation) or annotation
    if isinstance(origin, type) and issubclass(origin, FieldBlock):
        return origin
    return None


def settings_model(command: type[Command[Any]]) -> type[BaseModel] | None:
    """The settings model ``command`` declares as ``Command[S]``; ``None`` for plain ``Command``."""
    for klass in command.__mro__:
        metadata = getattr(klass, "__pydantic_generic_metadata__", None)
        if metadata and metadata.get("origin") is Command and metadata.get("args"):
            model = metadata["args"][0]
            return model if isinstance(model, type) else None
    return None


def derive[C: Command[Any] | Group](
    base: type[C],
    name: str,
    *,
    doc: str | None = None,
    omit: Iterable[str] = (),
    **fields: object,
) -> type[C]:
    """A subclass of a command or group with fields replaced or dropped, for generated classes.

    ``name`` is the class name (its command name follows, kebab-cased);
    ``doc`` replaces the docstring. For a command, a keyword naming a field
    replaces its type (keeping its description and default), any other
    keyword sets a class attribute (``kind=spec``), and ``omit`` drops fields.
    For a group, keywords set class attributes (``commands=(...)``). The one
    place a class name is data, because a generated class has no source to
    take it from.
    """
    if isinstance(base, type) and issubclass(base, Group):
        return type(
            name, (base,), {"__doc__": doc or base.__doc__, "__module__": base.__module__, **fields}
        )
    if not (isinstance(base, type) and issubclass(base, Command)):
        raise TypeError(f"derive() takes a Command or Group class, not {base!r}")
    return _derive_command(base, name, doc=doc, omit=tuple(omit), fields=fields)  # type: ignore[return-value]


def _derive_command(
    base: type[Command[Any]],
    name: str,
    *,
    doc: str | None,
    omit: tuple[str, ...],
    fields: dict[str, object],
) -> type[Command[Any]]:
    unknown = [field for field in omit if field not in base.model_fields]
    if unknown:
        raise TypeError(f"derive({base.__name__}, {name!r}): omit names no field {unknown}")
    replaced: dict[str, Any] = {}
    for field, annotation in fields.items():
        if field in base.model_fields:
            info = copy.copy(base.model_fields[field])
            info.annotation = annotation  # type: ignore[assignment]
            replaced[field] = (annotation, info)
    # An omitted field is shadowed by a class variable, so a subclass of the
    # derived class doesn't inherit it back from the base's annotations.
    dropped: dict[str, Any] = {field: (ClassVar[None], None) for field in omit}
    sub: type[Command[Any]] = create_model(
        name,
        __base__=base,
        __doc__=doc or base.__doc__,
        __module__=base.__module__,
        **replaced,
        **dropped,
    )
    for field, value in fields.items():
        if field not in base.model_fields:
            setattr(sub, field, value)
    return sub  # type: ignore[return-value]  # create_model types the class by its base


def generic_argument(annotation: object) -> object | None:
    """The type parameter of a generic block annotation (``Output[R]`` gives ``R``), if any."""
    args = get_args(annotation)
    return args[0] if args else None
