"""Back-compat tests for the deprecated ``untaped.api`` re-export shim.

``untaped.capability_api`` is the single SDK surface. ``untaped.api`` keeps
re-exporting every name it historically published (for one release) so
existing imports keep working.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import untaped.capability_api as capi

EXPECTED_SURFACE = frozenset(
    {
        # Tool execution context
        "AppContext",
        "app_context",
        "ThemeSpec",
        # Errors
        "UntapedError",
        "ConfigError",
        "HttpError",
        "HttpStatusError",
        "HttpTransportError",
        "first_validation_error",
        # HTTP
        "HttpClient",
        "HttpSettings",
        "RetryPolicy",
        "connected_client",
        "missing_setting_error",
        "paginate_link",
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
        "emit",
        "existing_directory",
        "existing_file",
        "parse_json_pairs",
        "parse_kv_pairs",
        "raise_usage",
        "render_rows",
        "report_errors",
        "resolve_each",
        # Git subprocesses
        "GitCommandError",
        "GitResult",
        "git_auth_header",
        "run_git",
        "safe_cache_path",
        "safe_path_segment",
        # Concurrency
        "bounded_map",
        # Settings access
        "get_config_section",
        "get_core_settings",
        "get_settings",
        "invalidate_settings_cache",
        # Filesystem/input helpers
        "FileChange",
        "FileWriteError",
        "apply_file_changes",
        "atomic_write",
        "read_structured_file",
        # Diff helpers
        "DiffStats",
        "diff_stats",
        "unified_diff_text",
        # Safe shared-config surface
        "ensure_config",
        "read_tool_state",
        "mutate_tool_state",
        # Tool-state helpers
        "StateCollection",
        "StateMap",
        # Interactive UI
        "ProgressHandle",
        "PromptChoice",
        "UiContext",
        "ui_context",
        # Stdin
        "read_identifiers",
        "read_records",
        "read_stdin",
        "read_stdin_text",
        "resolve_text_input",
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


def test_api_surface_matches_expected_sdk_names() -> None:
    api = importlib.import_module("untaped.api")
    actual = set(api.__all__)
    assert actual == EXPECTED_SURFACE, (
        "untaped.api surface drifted: "
        f"missing={sorted(EXPECTED_SURFACE - actual)}, "
        f"unexpected={sorted(actual - EXPECTED_SURFACE)}"
    )


def test_api_names_resolve() -> None:
    api = importlib.import_module("untaped.api")
    unresolved = [name for name in api.__all__ if not hasattr(api, name)]
    assert not unresolved, f"untaped.api.__all__ names that do not resolve: {unresolved}"


def test_shim_names_are_the_capability_api_objects() -> None:
    """Every name the shim shares with the canonical surface is the same object."""
    api = importlib.import_module("untaped.api")
    shared = set(api.__all__) & set(capi.__all__)
    for name in shared:
        assert getattr(api, name) is getattr(capi, name), name


def test_shim_import_emits_no_warning() -> None:
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import untaped.api"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""


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
    api = importlib.import_module("untaped.api")
    assert "install_skills" not in api.__all__
    assert not hasattr(api, "install_skills")
    assert "install_skills" not in capi.__all__
    assert not hasattr(capi, "install_skills")
