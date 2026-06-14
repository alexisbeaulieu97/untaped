"""Public API for the untaped core and plugin platform."""

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
from untaped.config_schema import (
    FieldDescriptor,
    find_descriptor,
    redact_secrets,
    secret_field_paths,
    walk_settings,
)
from untaped.errors import ConfigError, HttpError, UntapedError, first_validation_error
from untaped.http import HttpClient, resolve_verify
from untaped.output import OutputFormat, format_output
from untaped.pipe import PipeEnvelope, common_kind
from untaped.plugin_registry import SkillSpec

# Deprecated transitional v3 compat (plugin API v4 extracted profiles to the
# untaped-profile plugin): released v3-era plugins import these names, so
# they stay re-exported until the rollout completes across the plugin repos.
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    ProfileSource,
    classify_active_profile,
    effective_active_profile_name,
    resolve_profiles,
)
from untaped.progress import ProgressHandle
from untaped.prompts import (
    PromptChoice,
    confirm,
    multiselect,
    secret,
    select,
    text,
)
from untaped.settings import (
    HttpSettings,
    PluginInstallSpec,
    PluginsState,
    Settings,
    get_config_section,
    get_core_settings,
    get_profile_settings_model,
    get_settings,
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    resolve_config_path,
    splice_registered_state,
    validate_settings_isolated,
)
from untaped.stdin import read_identifiers, read_records, read_stdin
from untaped.ui import (
    BUILTIN_THEMES,
    ThemeSpec,
    UiContext,
    UiSettings,
    resolve_theme,
    ui_context,
)

__all__ = [
    "BUILTIN_THEMES",
    "DEFAULT_PROFILE",
    "ColumnsOption",
    "ConfigError",
    "FieldDescriptor",
    "FormatOption",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "OutputFormat",
    "PipeEnvelope",
    "PluginInstallSpec",
    "PluginsState",
    "ProfileOverrideOption",
    "ProfileSource",
    "ProgressHandle",
    "PromptChoice",
    "Settings",
    "SkillSpec",
    "ThemeSpec",
    "UiContext",
    "UiSettings",
    "UntapedError",
    "clamp_parallel",
    "classify_active_profile",
    "common_kind",
    "confirm",
    "create_app",
    "echo",
    "effective_active_profile_name",
    "existing_directory",
    "existing_file",
    "find_descriptor",
    "first_validation_error",
    "format_output",
    "get_config_section",
    "get_core_settings",
    "get_profile_settings_model",
    "get_settings",
    "get_settings_model",
    "multiselect",
    "parse_kv_pairs",
    "profile_override",
    "raise_usage",
    "read_identifiers",
    "read_records",
    "read_stdin",
    "redact_secrets",
    "register_profile_settings",
    "register_state_settings",
    "render_rows",
    "report_errors",
    "resolve_config_path",
    "resolve_each",
    "resolve_profiles",
    "resolve_theme",
    "resolve_verify",
    "secret",
    "secret_field_paths",
    "select",
    "splice_registered_state",
    "text",
    "ui_context",
    "validate_settings_isolated",
    "walk_settings",
]
