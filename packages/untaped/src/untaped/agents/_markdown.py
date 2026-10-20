"""The Markdown renderer of the self-description model: the pages ``untaped agents`` prints.

Every page opens with a heading and the line naming who answered (and, when
the folder's project asks for another untaped, the line saying so). Every
page below the map ends by pointing back at the map. Wording that is not a
generated fact stays here, in one place.
"""

from __future__ import annotations

from untaped.agents._model import (
    Answerer,
    CommandLine,
    CommandPage,
    Fill,
    GuideLine,
    GuidePage,
    Map,
    Mark,
    Option,
    Page,
    PluginPage,
    Writes,
)

FOOTER = "The map of this untaped: `untaped agents`"

_WRITES = {"write": " (changes things)", "destructive": " (destructive)"}

_INTRO = (
    "untaped is one command line built from plugins. Core (below) holds what every plugin "
    "shares: profiles, settings, tokens, output. Each plugin adds commands under "
    "`untaped PLUGIN`, and a plugin can offer extension points that other plugins fill.",
    "",
    "Read a plugin's page before running its commands: `untaped agents PLUGIN`, then "
    "`untaped agents PLUGIN COMMAND` for a command's options and examples.",
)


def render(page: Page) -> str:
    """The page as Markdown, ending in one newline."""
    if isinstance(page, Map):
        lines = _map(page)
    elif isinstance(page, PluginPage):
        lines = _plugin(page)
    elif isinstance(page, GuidePage):
        lines = _guide(page)
    else:
        lines = _command(page)
    return "\n".join(lines).rstrip() + "\n"


def _head(title: str, mark: Mark, answerer: Answerer) -> list[str]:
    parts = [f"untaped {answerer.untaped}"]
    if answerer.plugin is not None:
        parts.append(answerer.plugin)
    parts.append(answerer.environment)
    lines = [f"# {title}{_mark(mark)}", " · ".join(parts)]
    if answerer.mismatch is not None:
        lines.append(answerer.mismatch)
    return lines


def _mark(mark: Mark) -> str:
    return "" if mark is None else f" [{mark}]"


def _own_mark(mark: Mark, plugin: Mark) -> str:
    """A mark under a plugin's heading, left out when the heading already carries it."""
    return "" if mark == plugin else _mark(mark)


def _writes(writes: Writes) -> str:
    return "" if writes is None else _WRITES[writes]


def _fill(fill: Fill) -> str:
    return f"**{fill.plugin}**: {fill.summary}" if fill.summary else f"**{fill.plugin}**."


def _command_line(line: CommandLine) -> str:
    return f"- `{' '.join(line.path)}`{_mark(line.mark)}: {line.summary}{_writes(line.writes)}"


# --- stage 1 -------------------------------------------------------------------


def _map(page: Map) -> list[str]:
    shared = page.shared
    lines = [*_head("untaped", None, page.answerer), "", *_INTRO, "", "## Shared by every command"]
    lines += [
        f"- Settings live in `{shared.config_path}`: each plugin's under "
        "`profiles.<name>.<plugin>`. `untaped config` reads and changes them. A command "
        "missing a setting exits 4, and its hint names the other profiles that set it.",
        "- Tokens are kept by `untaped auth`; a command missing one exits 4 and names the fix.",
        "- Results are rows on stdout; progress and errors go to stderr, as JSON Lines under "
        "`--format json`. A command's own "
        "options, output formats and `--stdin` included where it has them, are on its page.",
        "- Without a terminal nothing prompts. A command that needs an answer exits 2, and "
        "its error names what to pass, such as `--yes`.",
        "- Options every command takes:",
        *(f"  {_option_line(option)}" for option in shared.options),
        "- Exit codes:",
        *(f"  - {code.code} {code.description}" for code in shared.exit_codes),
        "",
        "## untaped (core) · details: `untaped agents untaped`",
        *(f"- `{line.path[0]}`{_mark(line.mark)}: {line.summary}" for line in page.core),
    ]
    for plugin in page.plugins:
        lines += [
            "",
            f"## {plugin.name}{_mark(plugin.mark)} · details: `untaped agents {plugin.name}`",
        ]
        if plugin.summary:
            lines.append(plugin.summary)
        for point in plugin.points:
            lines.append(f"- {point.title}{_own_mark(point.mark, plugin.mark)}")
            lines += [f"  - {_fill(fill)}" for fill in point.fills]
        if plugin.extends:
            lines.append(f"- Extends: {', '.join(plugin.extends)}")
    return lines


# --- stage 2 -------------------------------------------------------------------


def _plugin(page: PluginPage) -> list[str]:
    lines = _head(page.name, page.mark, page.answerer)
    if page.summary:
        lines.append(page.summary)
    if page.guides:
        lines.append(
            f"Details of a topic (a command or a guide): `untaped agents {page.name} NAME`"
        )
    elif page.commands:
        lines.append(f"Details of a command: `untaped agents {page.name} COMMAND`")
    if page.own is not None:
        lines += _own(page.own)
    if page.commands:
        lines += ["", "## Commands", *(_command_line(line) for line in page.commands)]
    for point in page.points:
        lines += ["", f"## {point.title.rstrip('.')}{_own_mark(point.mark, page.mark)}"]
        lines += [
            f"- {_fill(fill)} Its settings: `untaped agents {fill.plugin}`" for fill in point.fills
        ] or ["- No installed plugin fills it."]
    for extends in page.extends:
        point = extends.point
        lines += ["", f"## Fills {point.owner}: {point.title.rstrip('.')}", f"- {extends.summary}"]
    if page.guides:
        lines += ["", "## Guides", *(_guide_line(guide) for guide in page.guides)]
    if page.settings:
        lines += ["", f"## Settings (`{page.settings_prefix}`)"]
        for setting in page.settings:
            shape = (
                setting.type
                if setting.default is None
                else f"{setting.type}, default `{setting.default}`"
            )
            text = f": {setting.description}" if setting.description else ""
            lines.append(f"- `{setting.key}`{_mark(setting.mark)} ({shape}){text}")
    return [*lines, "", FOOTER]


def _own(page: CommandPage) -> list[str]:
    """A plugin's own command on its page: what it does, its usage and examples."""
    lines = ["", "## Usage"]
    if page.summary:
        lines.append(f"{page.summary}{_writes(page.writes)}")
    if page.body:
        lines += ["", page.body]
    lines += ["", f"`{_usage(page)}`"]
    if page.options:
        lines += ["", *(_option_line(option) for option in page.options)]
    if page.examples:
        lines += ["", "## Examples", "", page.examples]
    return lines


def _guide_line(guide: GuideLine) -> str:
    shadowed = f" (shadowed by the command `{guide.name}`)" if guide.shadowed else ""
    text = f": {guide.description}" if guide.description else ""
    return f"- `{guide.name}`{shadowed}{text}"


# --- stage 3 -------------------------------------------------------------------


def _command(page: CommandPage) -> list[str]:
    title = " ".join((page.plugin, *page.path))
    lines = _head(title, page.mark, page.answerer)
    if page.summary:
        lines.append(f"{page.summary}{_writes(page.writes)}")
    if page.body:
        lines += ["", page.body]
    if page.runs:
        lines += ["", "## Usage", f"`{_usage(page)}`"]
        if page.options:
            lines += ["", *(_option_line(option) for option in page.options)]
    if page.subcommands:
        lines += ["", "## Commands", *(_command_line(line) for line in page.subcommands)]
    if page.examples:
        lines += ["", "## Examples", "", page.examples]
    return [*lines, "", FOOTER]


def _guide(page: GuidePage) -> list[str]:
    lines = _head(f"{page.plugin} {page.name}", page.mark, page.answerer)
    if page.description:
        lines.append(page.description)
    if page.body:
        lines += ["", page.body]
    return [*lines, "", FOOTER]


def _usage(page: CommandPage) -> str:
    words = list(page.invocation)
    for option in page.options:
        if option.positional:
            name = option.names[0] + ("..." if option.many else "")
            words.append(name if option.required else f"[{name}]")
        elif option.required:
            words.append(f"{option.names[0]} {'VALUE...' if option.many else 'VALUE'}")
    if any(not option.positional and not option.required for option in page.options):
        words.append("[OPTIONS]")
    return " ".join(words)


def _option_line(option: Option) -> str:
    names = ", ".join(
        f"`{name}...`" if option.positional and option.many else f"`{name}`"
        for name in option.names
    )
    text = option.help
    if option.choices:
        text = _sentence(text, "One of " + ", ".join(f"`{choice}`" for choice in option.choices))
    if option.default is not None and "default" not in option.help.lower():
        text = _sentence(text, f"Default: `{option.default}`")
    return f"- {names}: {text}" if text else f"- {names}"


def _sentence(text: str, addition: str) -> str:
    if not text:
        return f"{addition}."
    return f"{text if text.endswith('.') else text + '.'} {addition}."
