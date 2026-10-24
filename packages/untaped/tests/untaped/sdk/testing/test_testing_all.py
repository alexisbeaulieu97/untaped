"""Contract tests for the ``untaped.sdk.testing`` helper namespace."""

from __future__ import annotations

import importlib

import pytest

EXPECTED_ALL = [
    "CacheClock",
    "CliInvoker",
    "CliResult",
    "Home",
    "Install",
    "ManualClock",
    "MemoryCredentials",
    "MemoryStore",
    "PluginCandidate",
    "PluginCheckRow",
    "ProgressClock",
    "PromptBackend",
    "Ran",
    "ScreenKeys",
    "ScreenRun",
    "ScriptedPromptBackend",
    "ScriptedTerminal",
    "TtyStringIO",
    "assert_destructive_contract",
    "assert_fills",
    "assert_schema_pinned",
    "assert_surface",
    "cache_clock",
    "check",
    "check_conventions",
    "check_plugin",
    "compose_with",
    "current_memory_store",
    "damage",
    "drive_screen",
    "install",
    "invoke_cli",
    "invoke_root",
    "keep_value",
    "make_project",
    "manual_clock",
    "memory_store",
    "move_ask",
    "plan_release",
    "plugin_candidate",
    "progress_clock",
    "read_rows",
    "release_ask",
    "write_reference",
    "write_rows",
]


def test_testing_all_is_the_expected_surface() -> None:
    testing = importlib.import_module("untaped.sdk.testing")
    assert testing.__all__ == EXPECTED_ALL


def test_testing_reexports_prompt_backend() -> None:
    testing = importlib.import_module("untaped.sdk.testing")
    prompts = importlib.import_module("untaped.sdk.prompt.prompts")

    assert testing.PromptBackend is prompts.PromptBackend


@pytest.mark.usefixtures("fresh_composition")
def test_invoke_root_runs_the_composed_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """It composes the installed providers (here: none) and runs ``argv`` on the root."""
    from untaped.runtime import bootstrap
    from untaped.sdk.testing import invoke_root

    monkeypatch.setattr(bootstrap, "discover_candidates", lambda: [])
    result = invoke_root(["plugin", "list", "--format", "json"])
    assert (result.exit_code, result.stdout.strip()) == (0, "[]")
