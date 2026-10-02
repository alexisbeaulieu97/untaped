"""The composition root over every first-party capability: mounts, retired names, lazy help.

Moved from core's ``test_bootstrap.py``: these tests compose the installed
first-party capabilities, so they need every workspace package (Decision 5).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate
from untaped.settings import get_settings_model
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


def test_retired_orchestration_command_is_unknown(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(root.meta, ["orchestration"])

    assert result.exit_code == 2
    assert "orchestration" in result.output


def test_retired_orchestration_config_schema_is_absent(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    assert "orchestration" not in get_settings_model().model_fields

    result = CliInvoker().invoke(
        root.meta,
        ["config", "list", "--format", "raw", "--columns", "key"],
    )
    assert result.exit_code == 0, result.output
    assert not any(line.startswith("orchestration.") for line in result.stdout.splitlines())


def test_retired_orchestration_packaged_skill_is_absent(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)

    result = CliInvoker().invoke(
        root.meta,
        ["skills", "list", "--format", "raw", "--columns", "name"],
    )

    assert result.exit_code == 0, result.output
    assert "untaped-orchestration" not in result.stdout


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
