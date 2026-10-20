"""The self-description model: what an untaped says about itself, before any renderer.

Plain frozen data, built by :mod:`untaped.agents._build` from the composition
and read by :mod:`untaped.agents._markdown`. Facts are the same for every
reader; only a renderer decides how they look, so a later renderer
(``-f json``, an MCP transport, human help) reads this model too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type Mark = Literal["experimental"] | None
"""A stability mark a page shows; deprecated things are left out, as ``--help`` hides them."""

type Writes = Literal["write", "destructive"] | None
"""A command's declared ``@writes`` mark."""


@dataclass(frozen=True)
class Answerer:
    """Who answered: the untaped version, the plugin's distribution, the environment."""

    untaped: str
    environment: str
    """``sys.prefix``, with the home directory written ``~``."""
    plugin: str | None = None
    """``untaped-workspace 11.1.0`` on a plugin's pages; ``None`` on core's."""
    mismatch: str | None = None
    """The sentence saying the folder's project asks for another untaped, if it does."""


@dataclass(frozen=True)
class Option:
    """One positional or option of a command, as a caller spells it."""

    names: tuple[str, ...]
    help: str
    positional: bool = False
    required: bool = False
    many: bool = False
    default: str | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandLine:
    """A command as a list shows it: its path under the plugin, summary and marks."""

    path: tuple[str, ...]
    summary: str
    mark: Mark = None
    writes: Writes = None


@dataclass(frozen=True)
class Fill:
    """One plugin filling an extension point."""

    plugin: str
    summary: str


@dataclass(frozen=True)
class ExtensionPoint:
    """A contract a plugin owns, worded for its users, with whoever fills it."""

    owner: str
    title: str
    fills: tuple[Fill, ...] = ()
    mark: Mark = None


@dataclass(frozen=True)
class Extends:
    """An extension point of another plugin that this plugin fills."""

    point: ExtensionPoint
    summary: str


@dataclass(frozen=True)
class Setting:
    """One setting of a section, its key relative to the section."""

    key: str
    type: str
    default: str | None
    description: str
    mark: Mark = None


@dataclass(frozen=True)
class ExitCodeLine:
    code: int
    description: str


@dataclass(frozen=True)
class Shared:
    """What every command shares: the config file, the root options and the exit codes."""

    config_path: str
    options: tuple[Option, ...]
    exit_codes: tuple[ExitCodeLine, ...]


@dataclass(frozen=True)
class PluginEntry:
    """A plugin's section on the map: never more than its spec and the contracts say."""

    name: str
    summary: str
    mark: Mark
    points: tuple[ExtensionPoint, ...]
    extends: tuple[str, ...]


@dataclass(frozen=True)
class Map:
    """Stage 1, ``untaped agents``: who answered, the shared facts, core and every plugin."""

    answerer: Answerer
    shared: Shared
    core: tuple[CommandLine, ...]
    plugins: tuple[PluginEntry, ...]


@dataclass(frozen=True)
class PluginPage:
    """Stage 2, ``untaped agents PLUGIN``: its commands, extension points and settings."""

    answerer: Answerer
    name: str
    summary: str
    mark: Mark
    commands: tuple[CommandLine, ...]
    points: tuple[ExtensionPoint, ...]
    extends: tuple[Extends, ...]
    settings_prefix: str
    settings: tuple[Setting, ...]


@dataclass(frozen=True)
class CommandPage:
    """Stage 3, ``untaped agents PLUGIN COMMAND``: usage, options, behaviour and examples.

    A group answers with its subcommands; a group that runs on its own has a
    usage too.
    """

    answerer: Answerer
    plugin: str
    path: tuple[str, ...]
    invocation: tuple[str, ...]
    """The words before the options: ``untaped workspace archive``."""
    summary: str
    body: str
    mark: Mark
    writes: Writes
    runs: bool
    """Whether the command runs on its own (a group may only list subcommands)."""
    options: tuple[Option, ...]
    subcommands: tuple[CommandLine, ...]
    examples: str


type Page = Map | PluginPage | CommandPage
