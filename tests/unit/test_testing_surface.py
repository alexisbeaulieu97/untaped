"""Contract tests for the ``untaped.testing`` helper namespace."""

from __future__ import annotations

import importlib

EXPECTED_SURFACE = frozenset(
    {
        "CliInvoker",
        "CliResult",
        "PromptBackend",
        "ScriptedPromptBackend",
        "TtyStringIO",
        "assert_destructive_contract",
        "invoke_cli",
    }
)


def test_testing_declares_explicit_all() -> None:
    testing = importlib.import_module("untaped.testing")
    assert isinstance(testing.__all__, list)
    assert sorted(testing.__all__) == testing.__all__, "untaped.testing.__all__ must stay sorted"


def test_testing_surface_contains_expected_names() -> None:
    testing = importlib.import_module("untaped.testing")
    missing = EXPECTED_SURFACE - set(testing.__all__)
    assert not missing, f"untaped.testing is missing names: {sorted(missing)}"


def test_testing_names_resolve() -> None:
    testing = importlib.import_module("untaped.testing")
    unresolved = [name for name in testing.__all__ if not hasattr(testing, name)]
    assert not unresolved, f"untaped.testing.__all__ names that do not resolve: {unresolved}"


def test_testing_reexports_prompt_backend() -> None:
    testing = importlib.import_module("untaped.testing")
    prompts = importlib.import_module("untaped.prompts")

    assert testing.PromptBackend is prompts.PromptBackend
