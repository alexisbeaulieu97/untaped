"""Contract tests for the ``untaped.api`` plugin SDK namespace."""

from __future__ import annotations

import importlib

EXPECTED_SURFACE = frozenset(
    {
        # Plugin contract
        "CliSpec",
        "PluginManifest",
        "PluginRegistry",
        "RootOptionSpec",
        "SettingsLayout",
        "SettingsLayoutSpec",
        "UntapedPlugin",
        "SkillSpec",
        "DiagnosticResult",
        "ThemeSpec",
        # Plugin execution context
        "PluginContext",
        "plugin_context",
        # Errors
        "UntapedError",
        "ConfigError",
        "HttpError",
        "first_validation_error",
        # HTTP
        "HttpClient",
        "HttpSettings",
        "connected_client",
        "missing_setting_error",
        "paginate_offset",
        "paginate_pages",
        "resolve_verify",
        # CLI and output helpers
        "ColumnsOption",
        "FormatOption",
        "OutputFormat",
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
        "invalidate_settings_cache",
        # Deprecated transitional v3 compat (removal gated on the
        # plugin-API-v4 rollout finishing across the plugin repos)
        "ProfileOverrideOption",
        "profile_override",
        # Interactive UI
        "ProgressHandle",
        "PromptChoice",
        "UiContext",
        "ui_context",
        # Stdin
        "read_identifiers",
        "read_records",
        "read_stdin",
        # Pipe interchange
        "PipeEnvelope",
        "common_kind",
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


def test_api_keeps_v3_profile_compat_shims() -> None:
    """Profile selection moved to the untaped-profile plugin (API v4), but
    released v3 plugins still import these names at command dispatch; they
    stay as deprecated shims until the rollout completes (release-smoke
    regression, PR #273)."""
    api = importlib.import_module("untaped.api")
    for name in ("ProfileOverrideOption", "profile_override"):
        assert hasattr(api, name), f"untaped.api.{name} must stay importable"
        assert name in api.__all__


def test_api_exposes_get_settings_for_root_option_handlers() -> None:
    """Root-option handlers re-read settings after invalidating the cache."""
    api = importlib.import_module("untaped.api")
    assert callable(api.get_settings)
    assert callable(api.invalidate_settings_cache)
