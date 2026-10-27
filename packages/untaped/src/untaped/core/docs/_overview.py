"""The overview, ``untaped docs``: how the pages are organized, every plugin, what commands share.

Each plugin is listed with its help line and the extension points it fills;
"Shared by every command" is generated from the code that makes each line
true (the root options, the exit codes, ``Confirm``'s options for the
"(asks first)" legend).
"""

from __future__ import annotations

import inspect

from cyclopts import App

from untaped.core.docs._commands import mark
from untaped.core.docs._docstring import markdown
from untaped.core.docs._models import address
from untaped.core.docs._plugin import extends
from untaped.core.docs._site import CORE, Site
from untaped.runtime.settings import resolve_config_path
from untaped.sdk.errors import EXIT_CODE_PRECEDENCE, ExitCode
from untaped.sdk.fields import Confirm
from untaped.sdk.words import shown_path

#: Root options that don't change what a command does: left off the shared section.
_NOT_SHARED = frozenset({"--deprecated"})

_NAVIGATION = (
    "## Navigation",
    "",
    "Every page's title is its address: `untaped docs`, `untaped docs PLUGIN`, "
    "`untaped docs PLUGIN SECTION`, `untaped docs PLUGIN SECTION NAME`. Drop the last word "
    "to go up a level. Core's own commands are the plugin `untaped`.",
    "",
    "A plugin's sections are `commands` (what you can run), `inputs` (the files you write for "
    "its commands), `outputs` (the rows they print), `settings` and, for a plugin other "
    "plugins extend, `extensions`; its page lists the guides to read first "
    "(`untaped docs PLUGIN guides NAME`). Commands and guides are spelled as the command line "
    "spells them (`untaped docs untaped commands config set`), inputs and outputs as their "
    "kind spells itself{example}.",
    "",
    "`untaped docs [ADDRESS] --search TEXT` prints every line of the pages under the address "
    "that contains the text, after its page's address.",
)


def _navigation(site: Site) -> list[str]:
    """How addresses work, its kind example one this untaped prints."""
    kind = next((k for name in site.names() for k in site.outputs(name)), None)
    example = "" if kind is None else f" (`{kind.kind}` is `{address(kind, 'outputs')}`)"
    return [line.replace("{example}", example) for line in _NAVIGATION]


def page(site: Site) -> list[str]:
    """``untaped docs``."""
    lines = [*site.header(CORE), "", markdown(site.root.help or ""), "", *_navigation(site)]
    lines += ["", "## Plugins"]
    for name in site.names():
        plugin = site.plugin(name)
        stability = None if plugin is None else plugin.stability
        lines += ["", f"### {name}{mark(stability)}"]
        summary = site.summary(name)
        lines += [summary] if summary else []
        if plugin is not None:
            lines += extends(site, plugin, heading=False)
    lines += ["", "## Shared by every command", *shared(site.root)]
    return lines


def shared(root: App) -> list[str]:
    """What every command shares; each line generated from the code that makes it true."""
    options = [
        _root_option(argument)
        for argument in root.meta.assemble_argument_collection(parse_docstring=True)
        if argument.show
        and argument.field_info.kind is not inspect.Parameter.VAR_POSITIONAL
        and not _NOT_SHARED & set(argument.names)
    ]
    codes = [f"  - {int(code)} {markdown(code.description).rstrip('.')}" for code in ExitCode]
    yes, dry_run = Confirm.options
    return [
        f"- Settings live in `{shown_path(resolve_config_path())}`: each plugin's in its section "
        "at the top, and a named profile's under `profiles.<name>`. `untaped config` reads and "
        "changes them (keys are `<plugin>.<setting>`). A command missing a setting exits 4, and "
        "its hint names the other profiles that set it.",
        "- Tokens are kept by `untaped auth`; a command missing one exits 4 and names the fix.",
        "- Results are rows on stdout; progress and errors go to stderr, as JSON Lines under "
        "`--format json`. A command's own options, output formats and `--stdin` included where "
        "it has them, are on its page.",
        "- Without a terminal nothing prompts. A command that needs an answer exits 2, and its "
        "error names what to pass, such as `--yes`.",
        f"- (asks first), on a command's page: it asks before it changes anything. "
        f"`{yes.flag}`: {yes.help} `{dry_run.flag}`: {dry_run.help}",
        "- Options every command takes:",
        *options,
        "- Exit codes:",
        *codes,
        "  - A run that sees several failures exits with the most severe: "
        + " > ".join(str(int(code)) for code in EXIT_CODE_PRECEDENCE),
    ]


def _root_option(argument: object) -> str:
    spelled = sorted(getattr(argument, "names", ()), key=lambda name: not name.startswith("--"))
    names = ", ".join(f"`{name}`" for name in spelled)
    parameter = getattr(argument, "parameter", None)
    text = markdown(" ".join(inspect.cleandoc(getattr(parameter, "help", None) or "").split()))
    return f"  - {names}: {text}" if text else f"  - {names}"
