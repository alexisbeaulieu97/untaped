"""``untaped docs`` over the first-party composition: every page renders and says true things.

Every page under every address renders, titled with its address and naming
who answered. Every ``untaped docs ...`` a page quotes is a page; every other
``untaped ...`` it quotes parses. A command's page lists every option its
``--help`` shows, in the same order. The overview's "Shared by every command"
section names only options that exist. Core's own commands keep their
docstring ``Examples`` parsing too, as ``example-command`` holds every plugin's.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from cyclopts import App

from repo import quoted_commands
from untaped.core.docs import addresses, page
from untaped.core.docs._guide_files import guides
from untaped.core.docs._site import Site
from untaped.core.plugin_check.help_tree import example_violations
from untaped.runtime.bootstrap import build_root_app, composition
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("fresh_composition")

_OPTION = re.compile(r"`(-{1,2}[a-z][a-z0-9-]*)`")
_SHARED = re.compile(r"^## Shared by every command\n(.*?)^## ", re.MULTILINE | re.DOTALL)
#: An address a page quotes: ``untaped docs`` and lowercase words, no placeholder.
_ADDRESS = re.compile(r"`untaped docs((?: [a-z0-9_.-]+)*)`")
#: A row of a ``--help`` panel: its first name (``--repo``, ``NAME``).
_HELP_ROW = re.compile(r"^│ (?:\* +| {0,3})(\S+)")
_USAGE_ROW = re.compile(r"^- `([^`]+)`")
_PANEL = re.compile(r"^╭─ (.+?) ─")
_NOT_OWN = frozenset({"Commands", "Global options"})


def _help_rows(helped: str) -> list[str]:
    """The first name of each row of the command's own panels: no subcommand, no global option."""
    rows: list[str] = []
    panel = ""
    for line in helped.splitlines():
        if (title := _PANEL.match(line)) is not None:
            panel = title.group(1)
        elif panel not in _NOT_OWN and (row := _HELP_ROW.match(line)) is not None:
            rows.append(row.group(1))
    return rows


def _pages(root: App) -> Iterator[tuple[tuple[str, ...], str]]:
    result = composition()
    for address in addresses(Site(result, root)):
        yield address, page(root, result, address)


def test_every_page_renders_titled_with_its_address(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    seen = 0
    for address, text in _pages(root):
        title, header, *_ = text.splitlines()
        title = title.removesuffix(" [experimental]").removesuffix(" [deprecated]")
        assert title == "# " + " ".join(address or ("untaped",))
        assert header.startswith("untaped "), address
        seen += 1
    assert seen > 100, "the walk found too few pages"


def test_every_address_a_page_quotes_is_a_page(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    result = composition()
    known = set(addresses(Site(result, root)))
    missing = sorted(
        f"{' '.join(address)}: {quoted}"
        for address, text in _pages(root)
        for quoted in _ADDRESS.findall(text)
        if tuple(quoted.split()) not in known
    )
    assert missing == []


def test_every_command_a_page_quotes_parses(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    problems = sorted(
        f"{' '.join(address)}: {command}: {problem}"
        for address, text in _pages(root)
        for command, inline in quoted_commands.commands(text)
        if quoted_commands.argv_of(command)[0][:1] != ["docs"]
        and not command.endswith("[OPTIONS]")  # a usage line, not a command to run
        and (problem := quoted_commands.parse_problem(root, command, inline=inline)) is not None
    )
    assert problems == []


def test_every_command_page_lists_the_options_its_help_shows_in_order(
    first_party_candidates: tuple[PluginCandidate, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "300")
    root = build_root_app(candidates=first_party_candidates)
    site = Site(composition(), root)
    checked = 0
    for command in site.declaration.commands():
        words = site.declaration.words(command)
        address = (command.path[0], "commands", *command.path[1:])
        if command.path[0] == "untaped":
            address = ("untaped", "commands", *words)
        usage = page(root, composition(), address).split("## Usage\n", 1)[1]
        usage = usage.split("\n## ", 1)[0]
        listed = [m.group(1) for line in usage.splitlines() if (m := _USAGE_ROW.match(line))]
        helped = CliInvoker().invoke(root.meta, [*words, "--help"]).stdout
        assert [name.removesuffix("...") for name in listed] == _help_rows(helped), " ".join(words)
        checked += 1
    assert checked > 50


def test_every_first_party_guide_file_renders(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    result = composition()
    for plugin in result.plugins:
        for guide in guides(plugin.spec):
            text = page(root, result, [plugin.spec.name, "guides", guide.name])
            assert text.startswith(f"# {plugin.spec.name} guides {guide.name}\n"), guide.name
            assert guide.problem is None, f"{plugin.spec.name} {guide.name}: {guide.problem}"


def test_every_option_the_shared_section_names_exists(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    shared = _SHARED.search(page(root, composition(), []) + "## ")
    assert shared is not None
    known = {
        name
        for argument in root.meta.assemble_argument_collection(parse_docstring=False)
        for name in argument.names
    }
    known |= {
        name
        for command in composition().mount.declaration.commands()
        for option in command.options
        for name in (option.flag, option.short)
        if name is not None
    }
    named = set(_OPTION.findall(shared.group(1)))
    assert named, "the shared section names no option"
    assert named <= known, f"named but no command takes them: {sorted(named - known)}"


def test_core_examples_parse(first_party_candidates: tuple[PluginCandidate, ...]) -> None:
    root = build_root_app(candidates=first_party_candidates)
    plugins = {each.spec.name for each in composition().plugins if not each.root}
    core = [(name, root[name]) for name in root.resolved_commands() if name not in plugins]
    found = [
        f"{name}: {detail}" for name, app in core for _, detail in example_violations(root, app)
    ]
    assert found == []
