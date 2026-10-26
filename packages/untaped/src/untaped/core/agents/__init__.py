"""``untaped agents``: Markdown pages telling an agent what this untaped can do.

Plugins provide, untaped generates. The pages come from what plugins already
declare: ``PluginSpec.help``, the command tree, command docstrings and
option help, settings models, the contracts registry and the ``@writes``
marks, plus the guides a plugin writes for what no command says. There are
three stages: the map (``untaped agents``), one plugin (``untaped agents
PLUGIN``; core answers as ``untaped``) and one topic (``untaped agents PLUGIN
TOPIC``): a command, or a guide. A command wins over a guide of its name.
Every page names the untaped that answered, and says so when the folder's
project asks for another one.

This package is internal and imported only when ``untaped agents`` runs, so
root ``--help`` never pays for it.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib import metadata

from cyclopts import App

from untaped.core.agents._build import (
    CORE,
    build_command_page,
    build_guide_page,
    build_map,
    build_plugin_page,
)
from untaped.core.agents._markdown import render
from untaped.core.agents._requirement import UntapedRequirement, untaped_requirement
from untaped.runtime.registry import CompositionResult
from untaped.sdk.errors import UsageError
from untaped.sdk.words import shown_path


def page(root: App, result: CompositionResult, words: Sequence[str]) -> str:
    """The page ``untaped agents WORDS...`` prints; a ``UsageError`` that points at a list.

    A quoted path (``untaped agents jira "issues get"``) reads as the same
    words unquoted.
    """
    path = [part for word in words for part in word.split()]
    if not path:
        return render(build_map(root, result))
    name, rest = path[0], path[1:]
    plugin = next((each for each in result.plugins if each.spec.name == name), None)
    if plugin is None:
        raise UsageError(_not_installed(name, result))
    if not rest:
        return render(build_plugin_page(root, result, name))
    try:
        return render(build_command_page(root, result, name, rest))
    except KeyError:
        pass
    if not plugin.root and len(rest) == 1:
        try:
            return render(build_guide_page(result, name, rest[0]))
        except KeyError:
            pass
    raise UsageError(f'no topic "{" ".join(rest)}" in {name}; `untaped agents {name}` lists them')


def _not_installed(name: str, result: CompositionResult) -> str:
    record = next((each for each in result.quarantine if each.name == name), None)
    if record is not None:
        return (
            f"{name} is installed but was quarantined [{record.reason}]: {record.detail}; "
            "`untaped plugin list` shows it"
        )
    return (
        f"{name} is not installed in this untaped ({metadata.version(CORE)}, "
        f"{shown_path(sys.prefix)}); `untaped agents` lists what is"
    )


__all__ = ["UntapedRequirement", "page", "untaped_requirement"]
