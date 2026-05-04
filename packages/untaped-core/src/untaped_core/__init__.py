"""untaped-core: shared infrastructure for the untaped CLI suite.

Public API:

- ``settings``         — typed user configuration loaded from ``~/.untaped/config.yml``
- ``config_schema``    — walk Settings into a flat list of leaf ``FieldDescriptor``s
- ``config_file``      — read/write helpers for ``~/.untaped/config.yml``
- ``profile_resolver`` — merge ``profiles.default`` ⤥ ``profiles.<active>`` to an effective dict
- ``http``             — :class:`HttpClient` with unified error handling + ``resolve_verify``
- ``output``           — :func:`format_output` for json/yaml/table/raw rendering
- ``stdin``            — :func:`read_stdin` for piping
- ``cli``              — :func:`report_errors` to convert UntapedError → clean stderr
- ``errors``           — base exception hierarchy
"""

from untaped_core.cli import ColumnsOption, FormatOption, report_errors
from untaped_core.config_schema import redact_secrets, secret_field_paths
from untaped_core.errors import ConfigError, HttpError, UntapedError, first_validation_error
from untaped_core.http import HttpClient, resolve_verify
from untaped_core.output import OutputFormat, format_output
from untaped_core.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
    splice_workspace_registry,
)
from untaped_core.settings import HttpSettings, Settings, get_settings, resolve_config_path
from untaped_core.stdin import read_identifiers, read_stdin

__all__ = [
    "DEFAULT_PROFILE",
    "ColumnsOption",
    "ConfigError",
    "FormatOption",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "OutputFormat",
    "Settings",
    "UntapedError",
    "effective_active_profile_name",
    "first_validation_error",
    "format_output",
    "get_settings",
    "read_identifiers",
    "read_stdin",
    "redact_secrets",
    "report_errors",
    "resolve_config_path",
    "resolve_profiles",
    "resolve_verify",
    "secret_field_paths",
    "splice_workspace_registry",
]
