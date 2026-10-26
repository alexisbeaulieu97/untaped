"""``untaped agents`` over the first-party composition: every page renders and says true things.

Every plugin's page, every command's page and every guide renders. The map's "Shared by
every command" section is generated, and each option it names exists: a root
option, or one at least one composed command takes. Each command it quotes
parses. Core's own commands keep their docstring ``Examples`` parsing too, as
``example-command`` holds every plugin's.
"""

from __future__ import annotations

import re

import pytest
from cyclopts import App

from repo import quoted_commands
from untaped.core.agents import page
from untaped.core.agents._build import visible_children
from untaped.core.agents._guides import guides
from untaped.core.plugin_check.help_tree import example_violations
from untaped.runtime.bootstrap import build_root_app, composition
from untaped.runtime.registry import PluginCandidate

pytestmark = pytest.mark.usefixtures("fresh_composition")

_OPTION = re.compile(r"`(-{1,2}[a-z][a-z0-9-]*)`")
_SHARED = re.compile(r"^## Shared by every command\n(.*?)^## ", re.MULTILINE | re.DOTALL)


def _walk(app: App, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], App]]:
    found = []
    for name, sub in visible_children(app):
        found.append(((*path, name), sub))
        found.extend(_walk(sub, (*path, name)))
    return found


def _options(app: App) -> set[str]:
    return {
        name
        for argument in app.assemble_argument_collection(parse_docstring=False)
        for name in argument.names
    }


def test_every_plugin_and_command_page_renders(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    result = composition()
    assert page(root, result, []).startswith("# untaped\n")
    plugins = {each.spec.name for each in result.plugins if not each.root}
    for registered in result.plugins:
        plugin = registered.spec.name
        text = page(root, result, [plugin])
        assert text.startswith(f"# {plugin}"), plugin
        top = root if registered.root else root[plugin]
        for path, _ in _walk(top, ()):
            if registered.root and path[0] in plugins:
                continue
            command = page(root, result, [plugin, *path])
            assert command.endswith("The map of this untaped: `untaped agents`\n"), path


def test_every_first_party_guide_renders(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    result = composition()
    for plugin in result.plugins:
        for guide in guides(plugin.spec):
            text = page(root, result, [plugin.spec.name, guide.name])
            assert text.startswith(f"# {plugin.spec.name} {guide.name}"), guide.name
            assert guide.problem is None, f"{plugin.spec.name} {guide.name}: {guide.problem}"


def test_every_option_the_shared_section_names_exists(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    shared = _SHARED.search(page(root, composition(), []))
    assert shared is not None
    known = _options(root.meta)
    for _, app in _walk(root, ()):
        if app.default_command is not None:
            known |= _options(app)
    named = set(_OPTION.findall(shared.group(1)))
    assert named, "the shared section names no option"
    assert named <= known, f"named but no command takes them: {sorted(named - known)}"


def test_every_command_the_map_quotes_parses(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    text = page(root, composition(), [])
    problems = [
        f"{command}: {problem}"
        for command, inline in quoted_commands.commands(text)
        if (problem := quoted_commands.parse_problem(root, command, inline=inline)) is not None
    ]
    assert problems == []


def test_core_examples_parse(first_party_candidates: tuple[PluginCandidate, ...]) -> None:
    root = build_root_app(candidates=first_party_candidates)
    plugins = {each.spec.name for each in composition().plugins}
    core = [(path, app) for path, app in _walk(root, ()) if path[0] not in plugins]
    found = [
        f"{' '.join(path)}: {detail}"
        for path, app in core
        for _, detail in example_violations(root, app)
    ]
    assert found == []


def test_an_extension_point_repeats_no_mark_its_plugin_heading_carries(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    text = page(root, composition(), [])
    assert "- Where the repos you can name in workspace commands come from.\n" in text
    assert "- Git hosts untaped has credentials and a proxy for. [experimental]\n" in text
