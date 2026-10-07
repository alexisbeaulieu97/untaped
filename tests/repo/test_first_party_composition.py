"""The composition root over every first-party capability: mounts and lazy help.

Moved from core's ``test_bootstrap.py``: these tests compose the installed
first-party capabilities, so they need every workspace package (Decision 5).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate
from untaped.messages import EXPERIMENTAL_LINE
from untaped.testing import CliInvoker, provider_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_default_composition_is_the_first_party_capabilities(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    expected = tuple(candidate.name for candidate in first_party_candidates)

    composition = bootstrap.compose_root(candidates=first_party_candidates)

    assert tuple(capability.spec.name for capability in composition.capabilities) == expected
    assert composition.quarantine == ()

    root = bootstrap.build_root_app(candidates=first_party_candidates)
    for name in expected:
        result = CliInvoker().invoke(root.meta, [name, "--help"])
        assert result.exit_code == 0, result.output


def test_root_option_after_a_lazy_capability_name_is_not_a_command(
    first_party_candidates: tuple[ProviderCandidate, ...],
    _isolated_config: Path,
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, ["workspace", "--profile", "nope", "list"])

    assert result.exit_code == 4  # the active profile is not defined: config
    assert "Unknown command" not in result.stderr
    assert "'nope'" in result.stderr


def test_lazy_first_party_capabilities_render_like_eager_mounts(
    first_party_candidates: tuple[ProviderCandidate, ...],
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    specs = first_party_specs
    eager_candidates = [
        provider_candidate(replace(spec, help=None), distribution="untaped") for spec in specs
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
            "internals used by bootstrap._LazyCapabilityCommand may have drifted (see "
            "test_cyclopts_private_internals_used_by_lazy_mounts_exist)"
        )


def test_first_party_help_matches_app_summary(
    first_party_specs: tuple[CapabilitySpec, ...],
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


def test_experimental_capabilities_sit_in_the_root_experimental_panel(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, ["--help"])

    panels = _panels(result.stdout)
    assert list(panels)[-2:] == ["Experimental", "Parameters"]
    assert "Deprecated" not in panels
    experimental = panels["Experimental"]
    assert "workspace" in experimental and "dotfiles" in experimental
    assert "awx" not in experimental
    assert "workspace" not in panels["Commands"] and "dotfiles" not in panels["Commands"]


def test_the_deprecated_flag_adds_the_deprecated_panel_before_parameters(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout)

    assert list(panels) == ["Commands", "Experimental", "Deprecated", "Parameters"]
    assert "alias" in panels["Deprecated"] and "alias" not in panels["Commands"]


def test_a_deprecated_command_is_hidden_from_help_and_completion_but_stays_visible(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    # Hiding it would let the help-tree and stability walks skip it.
    assert root["alias"].show is not False
    completion = root.generate_completion(shell="bash")
    assert "alias" not in completion.split()
    assert "config" in completion


def test_awx_test_sits_in_awxs_experimental_panel(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    panels = _panels(CliInvoker().invoke(root.meta, ["awx", "--help"]).stdout)

    assert list(panels)[:1] == ["Commands"] and list(panels)[-2:] == ["Experimental", "Parameters"]
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
    first_party_candidates: tuple[ProviderCandidate, ...], argv: list[str]
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 0, result.output
    assert result.stdout.rstrip().endswith(EXPERIMENTAL_LINE)


@pytest.mark.parametrize("argv", [["awx", "jobs", "list", "--help"], ["awx", "ping", "--help"]])
def test_stable_commands_do_not_carry_the_experimental_line(
    first_party_candidates: tuple[ProviderCandidate, ...], argv: list[str]
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    assert EXPERIMENTAL_LINE not in CliInvoker().invoke(root.meta, argv).stdout
