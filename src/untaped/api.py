"""The supported plugin SDK surface.

Plugins import from this module (``from untaped.api import ...``) instead of
reaching into core internals. Names listed in ``__all__`` are the plugin API
contract: additions are backwards-compatible, removals or behaviour changes
require an ``untaped_api_version`` bump. Core modules stay free to reorganize
as long as this surface keeps resolving.
"""

from __future__ import annotations

# ProfileOverrideOption and profile_override are deprecated transitional v3
# compat (plugin API v4 moved profile selection to the untaped-profile
# plugin's root --profile option): released v3-era plugins import them at
# command dispatch, so they stay on this surface until the rollout completes
# across the plugin repos.
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    ProfileOverrideOption,
    clamp_parallel,
    create_app,
    echo,
    existing_directory,
    existing_file,
    parse_kv_pairs,
    profile_override,
    raise_usage,
    render_rows,
    report_errors,
    resolve_each,
)
from untaped.errors import ConfigError, HttpError, UntapedError, first_validation_error
from untaped.http import HttpClient, resolve_verify
from untaped.output import OutputFormat
from untaped.plugin_context import PluginContext, plugin_context
from untaped.plugin_http import (
    connected_client,
    missing_setting_error,
    paginate_offset,
    paginate_pages,
)
from untaped.plugin_registry import (
    CliSpec,
    DiagnosticResult,
    PluginManifest,
    PluginRegistry,
    RootOptionSpec,
    SettingsLayoutSpec,
    SkillSpec,
    UntapedPlugin,
)
from untaped.prompts import PromptChoice
from untaped.settings import (
    HttpSettings,
    get_config_section,
    get_core_settings,
    get_settings,
)
from untaped.settings_layout import SettingsLayout
from untaped.stdin import read_identifiers, read_stdin
from untaped.ui import ThemeSpec, UiContext, ui_context


def invalidate_settings_cache() -> None:
    """Drop the cached settings instance so the next read re-resolves.

    Root-option handlers (e.g. the profile plugin's ``--profile``) call this
    after changing process state that feeds settings resolution.
    """
    get_settings.cache_clear()


__all__ = [
    "CliSpec",
    "ColumnsOption",
    "ConfigError",
    "DiagnosticResult",
    "FormatOption",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "OutputFormat",
    "PluginContext",
    "PluginManifest",
    "PluginRegistry",
    "ProfileOverrideOption",
    "PromptChoice",
    "RootOptionSpec",
    "SettingsLayout",
    "SettingsLayoutSpec",
    "SkillSpec",
    "ThemeSpec",
    "UiContext",
    "UntapedError",
    "UntapedPlugin",
    "clamp_parallel",
    "connected_client",
    "create_app",
    "echo",
    "existing_directory",
    "existing_file",
    "first_validation_error",
    "get_config_section",
    "get_core_settings",
    "invalidate_settings_cache",
    "missing_setting_error",
    "paginate_offset",
    "paginate_pages",
    "parse_kv_pairs",
    "plugin_context",
    "profile_override",
    "raise_usage",
    "read_identifiers",
    "read_stdin",
    "render_rows",
    "report_errors",
    "resolve_each",
    "resolve_verify",
    "ui_context",
]
