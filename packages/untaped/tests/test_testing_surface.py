"""Contract tests for the ``untaped.testing`` helper namespace."""

from __future__ import annotations

import importlib

import pytest

EXPECTED_ALL = [
    "CliInvoker",
    "CliResult",
    "PromptBackend",
    "ScreenKeys",
    "ScreenRun",
    "ScriptedPromptBackend",
    "TtyStringIO",
    "assert_contract_schemas",
    "assert_destructive_contract",
    "assert_fills",
    "check_conventions",
    "compose_with",
    "drive_screen",
    "invoke_cli",
    "invoke_root",
    "plugin_candidate",
]


def test_testing_all_is_the_expected_surface() -> None:
    testing = importlib.import_module("untaped.testing")
    assert testing.__all__ == EXPECTED_ALL


def test_testing_reexports_prompt_backend() -> None:
    testing = importlib.import_module("untaped.testing")
    prompts = importlib.import_module("untaped.prompts")

    assert testing.PromptBackend is prompts.PromptBackend


@pytest.mark.usefixtures("fresh_composition")
def test_invoke_root_runs_the_composed_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """It composes the installed providers (here: none) and runs ``argv`` on the root."""
    from untaped import bootstrap
    from untaped.testing import invoke_root

    monkeypatch.setattr(bootstrap, "discover_candidates", lambda: [])
    result = invoke_root(["plugin", "list", "--format", "json"])
    assert (result.exit_code, result.stdout.strip()) == (0, "[]")
