"""Public API for the untaped core and plugin platform."""

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    clamp_parallel,
    parse_kv_pairs,
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
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    ProfileSource,
    classify_active_profile,
    effective_active_profile_name,
    resolve_profiles,
    splice_workspace_registry,
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
from untaped.stdin import read_identifiers, read_stdin

__all__ = [
    "DEFAULT_PROFILE",
    "ColumnsOption",
    "ConfigError",
    "FieldDescriptor",
    "FormatOption",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "OutputFormat",
    "PluginInstallSpec",
    "PluginsState",
    "ProfileSource",
    "Settings",
    "UntapedError",
    "clamp_parallel",
    "classify_active_profile",
    "effective_active_profile_name",
    "find_descriptor",
    "first_validation_error",
    "format_output",
    "get_config_section",
    "get_core_settings",
    "get_profile_settings_model",
    "get_settings",
    "get_settings_model",
    "parse_kv_pairs",
    "read_identifiers",
    "read_stdin",
    "redact_secrets",
    "register_profile_settings",
    "register_state_settings",
    "report_errors",
    "resolve_config_path",
    "resolve_each",
    "resolve_profiles",
    "resolve_verify",
    "secret_field_paths",
    "splice_registered_state",
    "splice_workspace_registry",
    "validate_settings_isolated",
    "walk_settings",
]
