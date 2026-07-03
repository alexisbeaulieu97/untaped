"""Contract tests for the ``untaped.api`` SDK namespace."""

from __future__ import annotations

import importlib

EXPECTED_SURFACE = frozenset(
    {
        # Tool composition contract
        "ToolSpec",
        "SkillAsset",
        "register_tool",
        "run_tool",
        "build_tool_app",
        # Tool execution context
        "AppContext",
        "app_context",
        "ThemeSpec",
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
        "parse_json_pairs",
        "parse_kv_pairs",
        "raise_usage",
        "render_rows",
        "report_errors",
        "resolve_each",
        # Concurrency
        "bounded_map",
        # Settings access
        "get_config_section",
        "get_core_settings",
        "get_settings",
        "invalidate_settings_cache",
        # Filesystem/input helpers
        "read_structured_file",
        # Safe shared-config surface
        "ensure_config",
        "read_tool_state",
        "mutate_tool_state",
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
        "is_envelope_line",
        "parse_envelope_line",
        # Batch mutation
        "batch_apply",
        "BatchOutcome",
        "finish",
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


def test_package_root_reexports_api_surface() -> None:
    """``from untaped import X`` must mirror ``from untaped.api import X``."""
    import untaped

    api = importlib.import_module("untaped.api")
    assert set(untaped.__all__) == set(api.__all__)
    for name in api.__all__:
        assert getattr(untaped, name) is getattr(api, name)


def test_api_drops_retired_plugin_contract() -> None:
    """The plugin platform is retired; its types must not resurface."""
    api = importlib.import_module("untaped.api")
    retired = {
        "CliSpec",
        "PluginManifest",
        "PluginRegistry",
        "RootOptionSpec",
        "SettingsLayoutSpec",
        "UntapedPlugin",
        "SkillSpec",
        "DiagnosticResult",
        "PluginContext",
        "plugin_context",
        "ProfileOverrideOption",
        "profile_override",
    }
    leaked = retired & set(api.__all__)
    assert not leaked, f"retired plugin contract still on the SDK surface: {sorted(leaked)}"


def test_api_exposes_get_settings_for_root_option_handlers() -> None:
    """Root-option handlers re-read settings after invalidating the cache."""
    api = importlib.import_module("untaped.api")
    assert callable(api.get_settings)
    assert callable(api.invalidate_settings_cache)


def test_skills_install_transaction_stays_off_public_api() -> None:
    """The install transaction is module-internal SDK plumbing, not tool API."""
    import untaped

    api = importlib.import_module("untaped.api")
    assert "install_skills" not in api.__all__
    assert not hasattr(api, "install_skills")
    assert "install_skills" not in untaped.__all__
    assert not hasattr(untaped, "install_skills")
