"""The ``commands`` section: its list, a command's page and a group's page.

A command's page is its declaration rendered: summary (with "(asks first)"
when it takes ``Confirm``), behaviour, a "Read first" line per guide it
links, ``## Usage`` with every option ``--help`` shows in the same panels
and order, what it reads and prints with the fields inline, and its
docstring's ``Examples``. Deprecated commands and options are left out of
lists and usage, as ``--help`` leaves them out; asked for by name, a
deprecated command still answers.
"""

from __future__ import annotations

from collections.abc import Sequence

from untaped.core.docs._docstring import describe, markdown
from untaped.core.docs._models import address as kind_address
from untaped.core.docs._models import field_lines
from untaped.core.docs._site import CORE, ORGANIZED, Site
from untaped.runtime.declaration import (
    CommandDecl,
    GroupDecl,
    OptionDecl,
    PluginDecl,
    StabilityDecl,
)
from untaped.sdk.marks import removal_text
from untaped.sdk.words import deprecated_line

#: What each return shape prints, as the ``## Outputs`` section words it.
_RETURNS = {
    "rows": "Rows of",
    "record": "One record of",
    "rows-or-record": "One record or a list of",
    "record-or-none": "One record, or nothing, of",
}
_LINES = {"line": "A line of text.", "line-or-none": "A line of text, or nothing."}


def mark(stability: StabilityDecl | None, inherited: StabilityDecl | None = None) -> str:
    """`` [experimental]`` or `` [deprecated]`` after a title or line, unless ``inherited`` is."""
    if stability is None or (inherited is not None and inherited.stability == stability.stability):
        return ""
    return f" [{stability.stability}]"


def _deprecation(site: Site, plugin: PluginDecl, stability: StabilityDecl | None) -> list[str]:
    """The line ``--help`` ends a deprecated command with: when it goes, and what replaces it."""
    if stability is None or stability.stability != "deprecated":
        return []
    distribution = None
    if plugin.name != CORE:
        distribution = site.result_plugin(plugin.name).plugin_ref.distribution
    removal = removal_text(stability.removed_in, distribution)
    return ["", deprecated_line(stability.replacement, removal)]


def _hidden(node: CommandDecl | GroupDecl) -> bool:
    return node.stability is not None and node.stability.stability == "deprecated"


# --- the list ----------------------------------------------------------------------


def count(plugin: PluginDecl) -> int:
    """The commands the list shows."""
    return len(_listed(plugin.commands)) + (plugin.runs is not None)


def _listed(nodes: tuple[CommandDecl | GroupDecl, ...]) -> list[CommandDecl]:
    found: list[CommandDecl] = []
    for node in nodes:
        if _hidden(node):
            continue
        if isinstance(node, GroupDecl):
            if node.runs is not None:
                found.append(node.runs)
            found.extend(_listed(node.commands))
        else:
            found.append(node)
    return found


def list_page(site: Site, plugin: PluginDecl) -> list[str]:
    """``untaped docs PLUGIN commands``: every command, under its group with the group's line."""
    lines = [
        *site.header(f"{plugin.name} commands{mark(plugin.stability)}", plugin.name),
        ORGANIZED,
        "",
        f"What you can run. One command: `untaped docs {plugin.name} commands COMMAND`.",
    ]
    return lines + _entries(plugin.commands, plugin.stability, plugin.runs)


def _entries(
    nodes: tuple[CommandDecl | GroupDecl, ...],
    inherited: StabilityDecl | None,
    runs: CommandDecl | None = None,
) -> list[str]:
    """``nodes``' commands (after the command their group runs), then each group's, headed."""
    own = [node for node in nodes if isinstance(node, CommandDecl) and not _hidden(node)]
    listed = [_entry(command, inherited) for command in ([runs] if runs else []) + own]
    lines = ["", *listed] if listed else []
    for group in (n for n in nodes if isinstance(n, GroupDecl) and not _hidden(n)):
        words = " ".join(group.path[1:])
        lines += ["", f"## {words}{mark(group.stability, inherited)}", markdown(group.summary)]
        lines += _entries(group.commands, group.stability or inherited, group.runs)
    return lines


def _entry(command: CommandDecl, inherited: StabilityDecl | None) -> str:
    words = " ".join(command.path[1:]) or command.path[0]  # a plugin's own command: its name
    return f"- `{words}`{mark(command.stability, inherited)}: {markdown(command.summary)}"


# --- one command or group ------------------------------------------------------------


def find(plugin: PluginDecl, words: Sequence[str]) -> CommandDecl | GroupDecl | None:
    """The command or group ``words`` names under ``plugin``; ``None`` when none does."""
    path = (plugin.name, *words)
    nodes: tuple[CommandDecl | GroupDecl, ...] = plugin.commands
    found: CommandDecl | GroupDecl | None = None
    for depth in range(1, len(path)):
        found = next((node for node in nodes if node.path[: depth + 1] == path[: depth + 1]), None)
        if found is None:
            return None
        nodes = found.commands if isinstance(found, GroupDecl) else ()
    return found


def page(site: Site, plugin: PluginDecl, node: CommandDecl | GroupDecl) -> list[str]:
    """A command's page, or a group's: its prose, then its own usage, then its commands."""
    title = " ".join((plugin.name, "commands", *node.path[1:]))
    inherited = _inherited(plugin, node)
    lines = [*site.header(f"{title}{mark(node.stability or inherited)}", plugin.name), ORGANIZED]
    runs = node.runs if isinstance(node, GroupDecl) else node
    described = describe(node.doc)
    summary = described.summary
    if isinstance(node, CommandDecl) and node.asks_first:
        summary = f"{summary} (asks first)".strip()
    lines += ["", summary] if summary else []
    lines += _deprecation(site, plugin, node.stability)
    lines += ["", described.body] if described.body else []
    examples = described.examples
    if runs is not None and runs is not node:
        ran = describe(runs.doc)
        head = f"{ran.summary} (asks first)" if runs.asks_first else ran.summary
        lines += [part for text in (head, ran.body) if text for part in ("", text)]
        examples = ran.examples or examples
    if runs is not None:
        lines += command_body(site, runs)
    if isinstance(node, GroupDecl):
        lines += ["", "## Commands", *(_entry(c, inherited) for c in _listed(node.commands))]
    if examples:
        lines += ["", "## Examples", "", examples]
    return lines


def _inherited(plugin: PluginDecl, node: CommandDecl | GroupDecl) -> StabilityDecl | None:
    """The mark the nearest marked group (or the plugin) puts on ``node``."""
    found = plugin.stability
    nodes = plugin.commands
    for depth in range(1, len(node.path) - 1):
        group = next(
            (n for n in nodes if isinstance(n, GroupDecl) and n.path == node.path[: depth + 1]),
            None,
        )
        if group is None:
            break
        found = group.stability or found
        nodes = group.commands
    return found


def command_body(site: Site, command: CommandDecl) -> list[str]:
    """What a command's page shows of its declaration: read first, usage, inputs, outputs."""
    lines: list[str] = []
    read_first = [guide for target in command.guides if (guide := site.guide(target))]
    lines += [""] if read_first else []
    lines += [f"Read first: `untaped docs {g.plugin} guides {g.name}`." for g in read_first]
    lines += ["", "## Usage", f"`{site.declaration.usage(command)}`"]
    lines += option_lines(command.options)
    lines += _files(site, "Inputs", command.inputs)
    lines += _files(site, "Writes", command.writes)
    lines += _outputs(site, command)
    return lines


def shown(option: OptionDecl) -> bool:
    """Whether ``--help`` shows ``option`` without ``--deprecated``."""
    return option.stability is None or option.stability.stability != "deprecated"


def option_lines(options: Sequence[OptionDecl]) -> list[str]:
    """Every option ``--help`` shows, in its panels, in their order, arguments first in each."""
    panels: dict[str, list[OptionDecl]] = {}
    for option in options:
        if shown(option):
            panels.setdefault(option.panel, []).append(option)
    lines: list[str] = []
    for panel, unsorted in panels.items():
        listed = sorted(unsorted, key=lambda option: not option.positional)
        # Options heads its panel only after another panel, as --help titles every one.
        lines += ["", f"### {panel}"] if panel != "Options" or lines else [""]
        lines += [option_line(option) for option in listed]
    return lines


def option_line(option: OptionDecl) -> str:
    """One option as a page lists it: its spellings, help, choices and default."""
    if option.positional:
        many = "..." if option.token.startswith("list[") else ""
        names = f"`{option.metavar}{many}`"
    else:
        spelled = [option.flag, option.negative, option.short]
        names = ", ".join(f"`{name}`" for name in spelled if name)
    text = markdown(" ".join(option.help.split()))
    says_default = "default" in text.lower()  # the help names it already
    if option.choices:
        text = _sentence(text, "One of " + ", ".join(f"`{choice}`" for choice in option.choices))
    default = _default(option)
    if default is not None and not says_default:
        text = _sentence(text, f"Default: `{default}`")
    return f"- {names}{mark(option.stability)}: {text}" if text else f"- {names}"


def _default(option: OptionDecl) -> str | None:
    value = option.default
    if option.required or value is None or value is False or value in ("", [], {}):
        return None
    if value is True:
        return "true"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _sentence(text: str, addition: str) -> str:
    if not text:
        return f"{addition}."
    return f"{text if text.endswith('.') else text + '.'} {addition}."


def _files(site: Site, heading: str, kinds: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    for name in kinds:
        kind = site.kind(name)
        if kind is None:
            continue
        lines += ["", f"`{kind.kind}` (`{kind_address(kind, 'inputs')}`): {markdown(kind.summary)}"]
        lines += field_lines(kind.fields, kind_address(kind, "inputs"))
    return ["", f"## {heading}", *lines[1:]] if lines else []


def _outputs(site: Site, command: CommandDecl) -> list[str]:
    lines: list[str] = []
    if command.returns in _LINES:
        lines += ["", _LINES[command.returns]]
    kind = None if command.output is None else site.kind(command.output)
    if kind is not None:
        shape = _RETURNS.get(command.returns, "Rows of")
        where = kind_address(kind, "outputs")
        lines += ["", f"{shape} `{kind.kind}` (`{where}`): {markdown(kind.summary)}"]
        lines += field_lines(kind.fields, where)
    accepted = [site.kind_of_target(target) for target in command.stdin]
    named = [each for each in accepted if each is not None]
    if named:
        listed = ", ".join(
            f"`{each.kind}` (`{kind_address(each, 'outputs')}`)"
            if site.printers(each.kind)
            else f"`{each.kind}`"
            for each in named
        )
        lines += ["", f"Accepts on stdin (`--stdin`): {listed}."]
    found = None if command.stdin_via is None else site.point(command.stdin_via)
    if found is not None:
        owner, point = found
        lines += [
            "",
            f"Also accepts on stdin each `{point.name}` provider's own records, read through "
            f"it (`untaped docs {owner.name} extensions {point.name}`).",
        ]
    return ["", "## Outputs", *lines[1:]] if lines else []
