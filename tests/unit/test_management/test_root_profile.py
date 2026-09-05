"""Tests for the root ``untaped profile …`` command group (Wave 1.4).

The profile surface is capability-agnostic (it operates on the shared
``profiles`` layout); the root group converts the per-tool group by naming
the unified executable in its guidance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.management.profile import build_root_profile_app
from untaped.profile.app import build_profile_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

_MANAGEMENT_VERBS = ("list", "show", "use", "current", "create", "delete", "rename")


def test_root_profile_exposes_all_verbs() -> None:
    app = build_root_profile_app(command="untaped")
    for verb in _MANAGEMENT_VERBS:
        assert verb in app


def test_root_profile_matches_per_tool_surface() -> None:
    """The root group converts the per-tool group: same verbs, same wiring."""
    root = build_root_profile_app(command="untaped")
    per_tool = build_profile_app("untaped")
    assert {name for name in root} == {name for name in per_tool}


def test_empty_hint_names_unified_executable() -> None:
    app = build_root_profile_app(command="untaped")
    result = CliInvoker().invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "`untaped profile create <name>`" in result.output


def test_list_marks_active_profile(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default: {}\n  work: {}\nactive: work\n", encoding="utf-8"
    )
    app = build_root_profile_app(command="untaped")
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["default", "work"]


def test_create_use_and_current_round_trip(_isolated_config: Path) -> None:
    app = build_root_profile_app(command="untaped")
    assert CliInvoker().invoke(app, ["create", "work"]).exit_code == 0
    use_result = CliInvoker().invoke(app, ["use", "work"])
    assert use_result.exit_code == 0, use_result.output
    assert "active profile: work" in use_result.output
    current = CliInvoker().invoke(app, ["current"])
    assert current.exit_code == 0, current.output
    assert current.stdout.strip() == "work"
