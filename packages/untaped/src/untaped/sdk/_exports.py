"""Every name ``untaped.sdk`` exports, imported from the module that defines it.

``untaped.sdk`` reads this table lazily (PEP 562), on the first name a plugin
asks for, so importing the package loads nothing else. The groups
(``untaped.sdk.storage``, ``network``, ``health``, ``git``, ``projects``, ``prompt``,
``contracts``, ``testing``) are their own modules: no name here is also a group's.
"""

from __future__ import annotations

from untaped.runtime.run_output import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.runtime.yaml_roundtrip import yaml_mapping_indent
from untaped.sdk._concurrency import bounded_map
from untaped.sdk._home import home
from untaped.sdk._keys import SettingKey, key
from untaped.sdk._run import (
    Context,
    Diag,
    PluginHandle,
    http_of,
    now,
    plugin_of,
    setting_name,
    settings_of,
)
from untaped.sdk._stdin import read_stdin_text
from untaped.sdk._terminal import Terminal
from untaped.sdk.diagnostic import (
    NOT_COUNTED,
    Diagnostic,
    Failure,
    Message,
    Progress,
    Stale,
    parse_utc,
)
from untaped.sdk.diff import unified_diff_text
from untaped.sdk.errors import (
    ConfigError,
    ErrorCategory,
    ExitCode,
    Failed,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    InvalidValue,
    Matched,
    OperationCancelledError,
    Service,
    System,
    UntapedError,
    UsageError,
    attribution,
    first_validation_error,
    most_severe,
)
from untaped.sdk.fields import (
    Age,
    AllowHyphen,
    Bytes,
    Column,
    Confirm,
    Document,
    DryRun,
    Duration,
    ExistingFile,
    File,
    Following,
    Key,
    KeyJson,
    KeyValue,
    Limit,
    Lines,
    ObjectName,
    Output,
    Parallel,
    PathOrStdin,
    Positional,
    PreviewItem,
    Refresh,
    Short,
    Spread,
    Status,
    Stdin,
    TextOrStdin,
    Verbatim,
)
from untaped.sdk.marks import (
    Deprecated,
    DeprecatedBehaviour,
    UntapedDeprecationWarning,
    deprecated,
    deprecated_parameters,
    experimental,
)
from untaped.sdk.plugin import Command, Group, Plugin, StubSkill, derive
from untaped.sdk.plugin_records import InstalledPlugin, installed_plugins
from untaped.sdk.records import (
    AbsolutePath,
    CheckRecord,
    Issued,
    OutcomeRecord,
    Record,
    Source,
    TargetRecord,
    UtcTimestamp,
    kind_of,
    record_model,
)
from untaped.sdk.storage.installed_code import installed_code
from untaped.sdk.tree import TreeGraph, TreeGraphEdge, TreeGraphNode, tree_text
from untaped.sdk.words import (
    ago_text,
    command_line,
    hint,
    not_found,
    plural,
    q,
    run_hint,
    shown_path,
    size_text,
    summary,
)

__all__ = [  # noqa: RUF022 — grouped by topic; order pinned by test_all_is_the_topic_groups_in_order
    # the plugin and its commands
    "Plugin",
    "Command",
    "Context",
    "Diag",
    "Group",
    "derive",
    "StubSkill",
    "Positional",
    "Short",
    "AllowHyphen",
    "Output",
    "Verbatim",
    "PreviewItem",
    "Stdin",
    "Lines",
    "Confirm",
    "DryRun",
    "Refresh",
    "Parallel",
    "Limit",
    "File",
    "TextOrStdin",
    "PathOrStdin",
    "ExistingFile",
    "KeyValue",
    "KeyJson",
    "Column",
    "Key",
    "Following",
    "Bytes",
    "Age",
    "Document",
    "Duration",
    "ObjectName",
    "Spread",
    "Status",
    "key",
    "SettingKey",
    "System",
    "Service",
    "settings_of",
    "setting_name",
    "plugin_of",
    "http_of",
    "PluginHandle",
    # composition
    "InstalledPlugin",
    "installed_plugins",
    # output and records
    "CheckRecord",
    "Diagnostic",
    "Diagnostics",
    "Issued",
    "Message",
    "NOT_COUNTED",
    "OutcomeRecord",
    "OutputFormat",
    "Progress",
    "Record",
    "Source",
    "Stale",
    "TargetRecord",
    "TreeGraph",
    "TreeGraphEdge",
    "TreeGraphNode",
    "UtcTimestamp",
    "now",
    "parse_utc",
    "kind_of",
    "record_model",
    "summary",
    "tree_text",
    "unified_diff_text",
    # errors
    "ConfigError",
    "ErrorCategory",
    "Failed",
    "Failure",
    "InvalidValue",
    "Matched",
    "OperationCancelledError",
    "UntapedError",
    "UsageError",
    "attribution",
    "command_line",
    "first_validation_error",
    "hint",
    "most_severe",
    "not_found",
    "run_hint",
    # input
    "AbsolutePath",
    "read_stdin_text",
    # concurrency
    "bounded_map",
    # http
    "HttpError",
    "HttpStatusError",
    "HttpTransportError",
    # git and filesystem
    "yaml_mapping_indent",
    # the home
    "home",
    # caches and stores
    "installed_code",
    # prompts and ui
    "Terminal",
    # conventions
    "Deprecated",
    "DeprecatedBehaviour",
    "ExitCode",
    "UntapedDeprecationWarning",
    "ago_text",
    "deprecated",
    "deprecated_parameters",
    "experimental",
    "plural",
    "q",
    "shown_path",
    "size_text",
]
