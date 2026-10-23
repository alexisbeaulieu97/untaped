"""The declaration: one plain model of every composed plugin, what core renders everything from.

:func:`untaped.runtime.composition.compose` builds it by walking the plugin
classes once. Every reader (help, ``untaped docs``, schemas, the surface
snapshot, the parser) reads this model and never a plugin class, so showing
help or docs needs no plugin import. Classes are named by their targets
(``module:Qualname``), which a run imports only to run that code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

if TYPE_CHECKING:
    from untaped.sdk.plugin import SpelledCommand


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StabilityDecl(_Frozen):
    """A stability mark on a plugin, group, command or field."""

    stability: Literal["experimental", "deprecated"]
    removed_in: int | None = None
    replacement: str | None = None


class OptionDecl(_Frozen):
    """One argument or flag of a command line, with what the parser and help need of it."""

    #: The field's path in the command: ``names``, or ``selection.names`` inside a bundle.
    field: str
    #: ``--timeout``; ``None`` for an argument.
    flag: str | None
    #: What help shows for the value: ``NAMES``, ``TIMEOUT``.
    metavar: str
    positional: bool = False
    #: The token shape the parser converts to: ``str``, ``int``, ``float``, ``bool``,
    #: ``list[str]``...
    token: str
    choices: tuple[str, ...] = ()
    negative: str | None = None
    short: str | None = None
    required: bool = False
    default: JsonValue = None
    help: str
    #: The help panel: ``Options`` for the command's own, a bundle's class name, or a block's.
    panel: str = "Options"
    #: The core-filled field this option belongs to (``output``, ``confirm``...), if any.
    block: str | None = None
    allow_hyphen: bool = False
    #: ``-`` reads the value from stdin (``TextOrStdin``, ``PathOrStdin``).
    or_stdin: bool = False
    stability: StabilityDecl | None = None


class AliasDecl(_Frozen):
    """An old spelling of a renamed command, which still works and warns until its removal."""

    name: str
    removed_in: int | None = None


#: What ``run``'s return type says the command prints.
type Returns = Literal[
    "rows", "record", "rows-or-record", "record-or-none", "line", "line-or-none", "nothing"
]


class CommandDecl(_Frozen):
    """One command: its words, options, prose and what it reads and prints."""

    node: Literal["command"] = "command"
    name: str
    #: The command's words under ``untaped``: ``("hello", "say")``.
    path: tuple[str, ...]
    target: str
    summary: str
    doc: str
    #: The section of the settings it reads (``Command[S]``), or ``None``.
    settings: str | None = None
    options: tuple[OptionDecl, ...] = ()
    returns: Returns
    #: The kind of the records it prints (``Output[R]``).
    output: str | None = None
    formats: tuple[str, ...] = ()
    #: The targets of the models ``--stdin`` accepts (``Stdin[R]``).
    stdin: tuple[str, ...] = ()
    #: The kinds of the documents it reads and writes (``File[M]``).
    inputs: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    #: The targets of the bundles and enums whose docstrings are guides it links "Read first".
    guides: tuple[str, ...] = ()
    asks_first: bool = False
    stability: StabilityDecl | None = None
    aliases: tuple[AliasDecl, ...] = ()


class GroupDecl(_Frozen):
    """A command group: its prose and its children."""

    node: Literal["group"] = "group"
    name: str
    path: tuple[str, ...]
    target: str
    summary: str
    doc: str
    stability: StabilityDecl | None = None
    commands: tuple[CommandDecl | GroupDecl, ...] = ()


class BlockDecl(_Frozen):
    """A block a plugin class holds, under the name derived from its attribute."""

    #: The kind of block (``cache``, ``stub-skill``...).
    block: str
    attribute: str
    #: The derived name: the attribute, or its kebab case for ids (``repo-store``).
    name: str
    #: The module that created the block, which the plugin's import loads.
    module: str


class KindDecl(_Frozen):
    """A record kind, ``<plugin>.<snake_case of the class>``, of the plugin that prints it."""

    kind: str
    target: str
    plugin: str


class PluginDecl(_Frozen):
    """One composed plugin."""

    name: str
    target: str
    #: The top-level package of the entry point's module: every class under it is the plugin's.
    package: str
    summary: str
    doc: str
    settings: str
    stability: StabilityDecl | None = None
    commands: tuple[CommandDecl | GroupDecl, ...] = ()
    #: The command ``untaped <plugin>`` runs on its own (``runs = Check``).
    runs: CommandDecl | None = None
    offers: tuple[str, ...] = ()
    fills: tuple[str, ...] = ()
    skill: str | None = None
    blocks: tuple[BlockDecl, ...] = ()


class Problem(_Frozen):
    """Why composition refused a plugin: a rule and what breaks it, naming the class or field."""

    rule: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"{self.where}: {self.detail} [{self.rule}]"


class Quarantined(_Frozen):
    """A plugin composition refused, with every problem it found."""

    name: str
    target: str
    problems: tuple[Problem, ...]


class Declaration(_Frozen):
    """Every composed plugin, every kind, and the plugins refused."""

    plugins: tuple[PluginDecl, ...] = ()
    kinds: tuple[KindDecl, ...] = ()
    quarantined: tuple[Quarantined, ...] = ()

    def plugin(self, name: str) -> PluginDecl:
        """The composed plugin ``name``; ``LookupError`` when there is none."""
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        raise LookupError(f"no composed plugin named {name!r}")

    def owner_of(self, module: str) -> str | None:
        """The plugin owning the classes defined in ``module`` (by its entry point's package)."""
        top = module.partition(".")[0]
        return next((plugin.name for plugin in self.plugins if plugin.package == top), None)

    def commands(self) -> list[CommandDecl]:
        """Every command, depth first, the plugins' roots included."""
        found: list[CommandDecl] = []

        def walk(nodes: tuple[CommandDecl | GroupDecl, ...]) -> None:
            for node in nodes:
                if isinstance(node, GroupDecl):
                    walk(node.commands)
                else:
                    found.append(node)

        for plugin in self.plugins:
            if plugin.runs is not None:
                found.append(plugin.runs)
            walk(plugin.commands)
        return found

    def section_of(self, settings_target: str) -> str | None:
        """The config section of the plugin whose settings model is ``settings_target``."""
        return next((p.name for p in self.plugins if p.settings == settings_target), None)

    def spell(self, spelled: SpelledCommand) -> str:
        """The command line a :class:`~untaped.sdk.plugin.SpelledCommand` stands for.

        A ``key(...)`` argument is spelled as its full dotted setting key.
        """
        from untaped.sdk.fields import SettingKey  # noqa: PLC0415 - only a spelled hint needs it

        target = target_of(spelled.command)
        command = next((c for c in self.commands() if c.target == target), None)
        if command is None:
            raise LookupError(f"{target} is not a composed command")
        words = ["untaped", *command.path]
        for arg in spelled.args:
            if isinstance(arg, SettingKey):
                section = self.section_of(target_of(SettingKey.model_of(arg)))
                words.append(".".join(p for p in (section, str(arg)) if p))
            else:
                words.append(str(arg))
        return " ".join(words)


GroupDecl.model_rebuild()


def target_of(obj: object) -> str:
    """``module:Qualname`` of a class or function: how the declaration names code."""
    return f"{getattr(obj, '__module__', '?')}:{getattr(obj, '__qualname__', repr(obj))}"
