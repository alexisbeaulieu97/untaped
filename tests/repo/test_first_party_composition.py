"""The composition root over every first-party plugin: mounts and lazy help.

Moved from core's ``test_bootstrap.py``: these tests compose the installed
first-party plugins, so they need every workspace package (Decision 5).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.messages import EXPERIMENTAL_LINE
from untaped.plugins.registry import PluginCandidate, PluginSpec
from untaped.stability import enable_show_deprecated, mark_of, panel_for, reset_show_deprecated
from untaped.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_default_composition_is_the_first_party_plugins(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    expected = tuple(candidate.name for candidate in first_party_candidates)

    composition = bootstrap.compose_root(candidates=first_party_candidates)

    assert tuple(plugin.spec.name for plugin in composition.plugins) == expected
    assert composition.quarantine == ()

    root = bootstrap.build_root_app(candidates=first_party_candidates)
    for name in expected:
        result = CliInvoker().invoke(root.meta, [name, "--help"])
        assert result.exit_code == 0, result.output


def test_root_option_after_a_lazy_plugin_name_is_not_a_command(
    first_party_candidates: tuple[PluginCandidate, ...],
    _isolated_config: Path,
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, ["workspace", "--profile", "nope", "list"])

    assert result.exit_code == 4  # the active profile is not defined: config
    assert "Unknown command" not in result.stderr
    assert "'nope'" in result.stderr


def test_lazy_first_party_plugins_render_like_eager_mounts(
    first_party_candidates: tuple[PluginCandidate, ...],
    first_party_specs: tuple[PluginSpec, ...],
) -> None:
    specs = first_party_specs
    eager_candidates = [
        plugin_candidate(replace(spec, help=None), distribution="untaped") for spec in specs
    ]
    argv_cases = [["--help"]] + [
        [spec.name, flag] for spec in specs for flag in ("--help", "--version")
    ]
    for argv in argv_cases:
        lazy = CliInvoker().invoke(
            bootstrap.build_root_app(candidates=first_party_candidates).meta, argv
        )
        eager = CliInvoker().invoke(
            bootstrap.build_root_app(candidates=eager_candidates).meta, argv
        )
        assert (lazy.exit_code, lazy.output) == (eager.exit_code, eager.output), (
            f"lazy mount of {argv} renders differently from an eager mount; cyclopts "
            "internals used by bootstrap._LazyPluginCommand may have drifted (see "
            "test_cyclopts_private_internals_used_by_lazy_mounts_exist)"
        )


def test_first_party_help_matches_app_summary(
    first_party_specs: tuple[PluginSpec, ...],
) -> None:
    for spec in first_party_specs:
        assert spec.help is not None, spec.name
        assert spec.help == spec.app_factory().help, spec.name


def _panels(text: str) -> dict[str, str]:
    """``{panel title: panel text}`` of a rendered ``--help``."""
    panels: dict[str, str] = {}
    title = ""
    for line in text.splitlines():
        if line.startswith("╭"):
            title = line.strip("╭─ ").split(" ─")[0]
            panels[title] = ""
        elif title:
            panels[title] += line + "\n"
    return panels


def _rows(panel: str) -> list[str]:
    """The first word of each row of a panel (wrapped continuation lines skipped)."""
    return [
        line[2:].split()[0]
        for line in panel.splitlines()
        if line.startswith("│ ") and line[2:3] != " " and not line.startswith("╰")
    ]


def test_each_root_command_sits_in_exactly_one_panel(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    composition = bootstrap.composition()
    plugins = {plugin.spec.name: plugin.spec for plugin in composition.plugins}
    expected: dict[str, str] = {}
    for name in root:
        if name.startswith("-"):
            continue
        spec = plugins.get(name)
        mark = spec.stability if spec is not None else mark_of(root[name])
        if mark is not None:
            expected[name] = panel_for(mark).name
        else:
            expected[name] = "Plugins" if spec is not None else "Commands"

    def placed() -> dict[str, str]:
        text = CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout
        return {
            row: title
            for title, panel in _panels(text).items()
            if title != "Global options"
            for row in _rows(panel)
        }

    fresh = CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout
    assert placed() == expected, (
        "root help panels are core's to assign: a plugin goes in Plugins (or its "
        "stability panel) through bootstrap._root_panel, every other root command in "
        "Commands unless marked"
    )
    for name in plugins:
        root[name]  # resolve each lazy plugin, as dispatch or completion would
    assert CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout == fresh, (
        "resolving a plugin moved it to another panel; see bootstrap._place"
    )


def test_root_help_lists_core_then_plugins_then_global_options(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["--help"]).stdout)

    assert list(panels) == ["Commands", "Plugins", "Experimental", "Global options"]
    assert {"awx", "jira", "ansible"} <= set(_rows(panels["Plugins"]))
    assert {"workspace", "dotfiles"} <= set(_rows(panels["Experimental"]))
    assert _rows(panels["Global options"]) == [
        "--profile",
        "--verbose",
        "--quiet",
        "--deprecated",
        "--help",
        "--version",
        "--install-completion",
    ]


@pytest.mark.parametrize(
    "argv", [["config", "list", "--help"], ["awx", "jobs", "list", "--help"], ["awx", "--help"]]
)
def test_global_options_are_the_last_panel_of_every_help(
    first_party_candidates: tuple[PluginCandidate, ...], argv: list[str]
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, argv).stdout)

    assert list(panels)[-1] == "Global options"
    assert _rows(panels["Global options"]) == ["--profile", "--verbose", "--quiet", "--deprecated"]
    for title, panel in panels.items():
        if title != "Global options":
            assert not {"--profile", "--verbose", "--quiet"} & set(_rows(panel)), title


def test_a_plugin_section_lists_before_global_options(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["ansible", "graph", "--help"]).stdout)

    assert list(panels)[-2:] == ["Source Data", "Global options"]


def test_the_deprecated_flag_adds_the_deprecated_panel_before_global_options(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout)

    assert list(panels) == ["Commands", "Plugins", "Experimental", "Deprecated", "Global options"]
    assert "alias" in panels["Deprecated"] and "alias" not in panels["Commands"]


def test_a_deprecated_command_is_hidden_from_help_and_completion_but_stays_visible(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    # Hiding it would let the help-tree and stability walks skip it.
    assert root["alias"].show is not False
    completion = root.generate_completion(shell="bash")
    assert '"alias"' not in completion
    assert '"config"' in completion
    token = enable_show_deprecated()
    try:
        # The same call lists it once the flag is on, so the check above can fail.
        assert '"alias"' in root.generate_completion(shell="bash")
    finally:
        reset_show_deprecated(token)


def test_awx_test_sits_in_awxs_experimental_panel(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["awx", "--help"]).stdout)

    assert list(panels) == ["Commands", "Experimental", "Global options"]
    assert panels["Experimental"].split()[1] == "test"
    assert "test" not in panels["Commands"].split()


@pytest.mark.parametrize(
    "argv",
    [
        ["workspace", "--help"],
        ["workspace", "list", "--help"],
        ["dotfiles", "--help"],
        ["dotfiles", "status", "--help"],
        ["awx", "test", "--help"],
        ["awx", "test", "run", "--help"],
    ],
)
def test_every_experimental_help_ends_with_the_experimental_line(
    first_party_candidates: tuple[PluginCandidate, ...], argv: list[str]
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 0, result.output
    assert result.stdout.rstrip().endswith(EXPERIMENTAL_LINE)


@pytest.mark.parametrize("argv", [["awx", "jobs", "list", "--help"], ["awx", "ping", "--help"]])
def test_stable_commands_do_not_carry_the_experimental_line(
    first_party_candidates: tuple[PluginCandidate, ...], argv: list[str]
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    assert EXPERIMENTAL_LINE not in CliInvoker().invoke(root.meta, argv).stdout
