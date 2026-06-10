"""Contract tests for the ``untaped.api`` plugin SDK namespace."""

from __future__ import annotations

import importlib

EXPECTED_SURFACE = frozenset(
    {
        # Plugin contract
        "PluginRegistry",
        "UntapedPlugin",
        "SkillSpec",
        "DiagnosticResult",
        "ThemeSpec",
        # Errors
        "UntapedError",
        "ConfigError",
        "HttpError",
        "first_validation_error",
        # HTTP
        "HttpClient",
        "HttpSettings",
        "resolve_verify",
        # CLI and output helpers
        "ColumnsOption",
        "FormatOption",
        "OutputFormat",
        "ProfileOverrideOption",
        "clamp_parallel",
        "create_app",
        "echo",
        "existing_directory",
        "existing_file",
        "parse_kv_pairs",
        "raise_usage",
        "render_rows",
        "report_errors",
        "resolve_each",
        # Settings access
        "get_config_section",
        "get_core_settings",
        "profile_override",
        # Interactive UI
        "PromptChoice",
        "UiContext",
        "ui_context",
        # Stdin
        "read_identifiers",
        "read_stdin",
    }
)


def test_api_declares_explicit_all() -> None:
    api = importlib.import_module("untaped.api")
    assert isinstance(api.__all__, list)
    assert sorted(api.__all__) == api.__all__, "untaped.api.__all__ must stay sorted"


def test_api_surface_contains_sdk_names() -> None:
    api = importlib.import_module("untaped.api")
    missing = EXPECTED_SURFACE - set(api.__all__)
    assert not missing, f"untaped.api is missing SDK names: {sorted(missing)}"


def test_api_names_resolve() -> None:
    api = importlib.import_module("untaped.api")
    unresolved = [name for name in api.__all__ if not hasattr(api, name)]
    assert not unresolved, f"untaped.api.__all__ names that do not resolve: {unresolved}"
