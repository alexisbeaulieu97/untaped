"""Every name ``untaped.sdk`` exports today, imported from the module that defines it.

``untaped.sdk`` reads this table lazily (PEP 562), on the first name a plugin
asks for, so importing the package loads nothing else.
The table shrinks as plugins move to the 12.0 shape and import each name from
its group (``untaped.sdk.storage``, ``untaped.sdk.network``, ...).

The screen names (``Screen``, ``Cmd``, the components such as
``TextInput``, ...) are lazy here too, so a plugin that asks for ``Cache``
loads no screen code and no prompt_toolkit; they are experimental
(``docs/versioning.md``). So is core's data file (``read_data_file``,
``write_data_file``, ...), loaded when a command first uses it.
"""

from __future__ import annotations

import importlib as _importlib
import typing as _typing

from untaped.runtime.registry import PluginSpec, SkillAsset, plugin_dir
from untaped.runtime.settings import HttpSettings
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat, TreeOutputFormat
from untaped.runtime.yaml_roundtrip import yaml_mapping_indent
from untaped.screen.picker import (
    PickCatalog,
    Picked,
    PickItem,
    PickRequest,
    PickResult,
    PickSetting,
)
from untaped.sdk._app_context import AppContext, app_context
from untaped.sdk._batch import BatchOutcome, batch_apply, finish
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    LimitOption,
    ParallelOption,
    StdinOption,
    TreeFormatOption,
    YesOption,
    check_columns,
    clamp_parallel,
    create_app,
    echo,
    emit,
    emit_document,
    existing_file,
    parse_json_pairs,
    parse_kv_pairs,
    raise_usage,
    render_rows,
    resolve_each,
    writes,
)
from untaped.sdk._concurrency import bounded_map
from untaped.sdk._home import home, plugin_path
from untaped.sdk._run import (
    Context,
    Diag,
    PluginHandle,
    http_of,
    now,
    plugin_of,
    settings_of,
)
from untaped.sdk._stdin import read_stdin, read_stdin_text
from untaped.sdk._terminal import Terminal
from untaped.sdk.diagnostic import (
    NOT_COUNTED,
    Debug,
    Diagnostic,
    Failure,
    Message,
    Progress,
    Stale,
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
    Matched,
    NeedsYesError,
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
    SettingKey,
    Short,
    Spread,
    Status,
    Stdin,
    TextOrStdin,
    Verbatim,
    Written,
    key,
    table_row,
)
from untaped.sdk.git import (
    GitCommandError,
    GitResult,
    git_toplevel,
    run_git,
    safe_path_segment,
)
from untaped.sdk.health.deprecated_keys import Renamed, Retired, retired_values
from untaped.sdk.health.doctor import (
    DoctorCheck,
    DoctorResult,
    ExecutableCheck,
    OnlineCheck,
    PluginContext,
    executable_check,
    online_check,
)
from untaped.sdk.health.marks import NeedsSetup, Suggest
from untaped.sdk.marks import (
    DEPRECATED_OPTIONS,
    Deprecated,
    DeprecatedBehaviour,
    UntapedDeprecationWarning,
    deprecated,
    deprecated_alias,
    deprecated_behaviour,
    deprecated_parameters,
    experimental,
)
from untaped.sdk.network.auth import find_program
from untaped.sdk.network.credentials import (
    BoundSecret,
    Credential,
    CredentialKey,
    NoCredential,
    TlsVerify,
    TokenUrl,
)
from untaped.sdk.network.http import (
    HttpClient,
    HttpFactory,
    RetryPolicy,
    connected_client,
    paginate_link,
    paginate_offset,
    paginate_pages,
    rejected_token_error,
    resolve_verify,
    same_origin,
)
from untaped.sdk.plugin import Command, Group, Plugin, StubSkill, derive
from untaped.sdk.plugin_records import InstalledPlugin, installed_plugins
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.projects import (
    Project,
    checkout_root,
    committed_path,
    current_project,
    known_projects,
    nearest_pyproject,
    project_at,
)
from untaped.sdk.prompt.editor import run_editor
from untaped.sdk.prompt.prompts import PromptChoice
from untaped.sdk.records import (
    AbsolutePath,
    CheckRecord,
    Issued,
    OutcomeRecord,
    Record,
    Source,
    TableGlyph,
    TableText,
    TargetRecord,
    UtcTimestamp,
    kind_of,
    record_model,
)
from untaped.sdk.storage._fs import atomic_write, dir_bytes, file_lock, read_structured_file
from untaped.sdk.storage._refs import RefHolder, RefTarget, holders
from untaped.sdk.storage.cache import (
    Cache,
    Filled,
    Freshness,
    FreshnessRecord,
    NeverRefreshed,
    Served,
    code_version,
    refresh_option,
)
from untaped.sdk.storage.holds import (
    HolderState,
    HolderView,
    ItemStore,
    LetGo,
    MoveAsk,
    Refused,
    ReleaseAsk,
    UnknownHolder,
)
from untaped.sdk.storage.state import (
    ProjectStateRows,
    RowExists,
    StateRows,
)
from untaped.sdk.storage.store import Store, setting_guard
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

if _typing.TYPE_CHECKING:
    from untaped.screen.components.buttons import Button, Buttons, Pressed
    from untaped.screen.components.choices import (
        Check,
        Cycle,
        ListItem,
        MultiList,
        Select,
        SingleList,
    )
    from untaped.screen.components.fields import Field
    from untaped.screen.components.form import Form, Submitted
    from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
    from untaped.screen.components.layout import Panes, Viewport
    from untaped.screen.components.lists import SearchList, Tags, Tree, TreeRow
    from untaped.screen.components.tabs import Tab, Tabs
    from untaped.screen.core import (
        Activate,
        Back,
        Binding,
        Cancel,
        Cmd,
        CmdError,
        Footer,
        Frame,
        Help,
        Interrupt,
        NextField,
        Paste,
        PrevField,
        Quit,
        Resize,
        Screen,
        Submit,
    )
    from untaped.sdk.storage.data_file import (
        Absent,
        DataFile,
        DataRead,
        Found,
        OtherVersion,
        Unreadable,
        read_data_file,
        write_data_file,
    )
    from untaped.sdk.storage.dir import Dir
    from untaped.sdk.storage.installed_code import installed_code
    from untaped.sdk.storage.lock import Lock

#: Names exported lazily, by the module that defines them, resolved on first access
#: (PEP 562): the screen names, core's data file, which a command reads on first use,
#: the installed code's version, which a cache reads when it is served, and the
#: operation lock and the plugin's places, which a plugin takes when it runs.
_LAZY_MODULES: dict[str, tuple[str, ...]] = {
    "untaped.sdk.storage.dir": ("Dir",),
    "untaped.sdk.storage.installed_code": ("installed_code",),
    "untaped.sdk.storage.lock": ("Lock",),
    "untaped.sdk.storage.data_file": (
        "Absent",
        "DataFile",
        "DataRead",
        "Found",
        "OtherVersion",
        "Unreadable",
        "read_data_file",
        "write_data_file",
    ),
    "untaped.screen.core": (
        "Activate",
        "Back",
        "Binding",
        "Cancel",
        "Cmd",
        "CmdError",
        "Footer",
        "Frame",
        "Help",
        "Interrupt",
        "NextField",
        "Paste",
        "PrevField",
        "Quit",
        "Resize",
        "Screen",
        "Submit",
    ),
    "untaped.screen.components.inputs": ("NumberInput", "PathInput", "SecretInput", "TextInput"),
    "untaped.screen.components.choices": (
        "Check",
        "Cycle",
        "ListItem",
        "MultiList",
        "Select",
        "SingleList",
    ),
    "untaped.screen.components.tabs": ("Tab", "Tabs"),
    "untaped.screen.components.buttons": ("Button", "Buttons", "Pressed"),
    "untaped.screen.components.fields": ("Field",),
    "untaped.screen.components.form": ("Form", "Submitted"),
    "untaped.screen.components.layout": ("Panes", "Viewport"),
    "untaped.screen.components.lists": ("SearchList", "Tags", "Tree", "TreeRow"),
}
_LAZY_EXPORTS: dict[str, str] = {
    name: module for module, names in _LAZY_MODULES.items() for name in names
}

__all__ = [  # noqa: RUF022 — grouped by topic; order pinned by test_all_is_the_topic_groups_in_order
    # the plugin and its commands (12.0)
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
    "Written",
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
    "TableText",
    "table_row",
    "key",
    "SettingKey",
    "System",
    "Service",
    "settings_of",
    "plugin_of",
    "http_of",
    "PluginHandle",
    # composition
    "PluginContext",
    "PluginSpec",
    "InstalledPlugin",
    "installed_plugins",
    "DoctorCheck",
    "ExecutableCheck",
    "OnlineCheck",
    "DoctorResult",
    "SkillAsset",
    "create_app",
    "plugin_dir",
    # settings and state
    "AppContext",
    "ProjectStateRows",
    "RowExists",
    "StateRows",
    "app_context",
    # output and records
    "CheckRecord",
    "ColumnsOption",
    "Debug",
    "Diagnostic",
    "Diagnostics",
    "FormatOption",
    "Issued",
    "Message",
    "NOT_COUNTED",
    "OutcomeRecord",
    "OutputFormat",
    "Progress",
    "Record",
    "Source",
    "Stale",
    "TableGlyph",
    "TargetRecord",
    "TreeFormatOption",
    "TreeGraph",
    "TreeGraphEdge",
    "TreeGraphNode",
    "TreeOutputFormat",
    "UtcTimestamp",
    "now",
    "check_columns",
    "echo",
    "emit",
    "emit_document",
    "finish",
    "kind_of",
    "record_model",
    "render_rows",
    "summary",
    "tree_text",
    "unified_diff_text",
    # errors
    "ConfigError",
    "ErrorCategory",
    "Failed",
    "Failure",
    "Matched",
    "NeedsYesError",
    "OperationCancelledError",
    "UntapedError",
    "UsageError",
    "attribution",
    "command_line",
    "first_validation_error",
    "hint",
    "most_severe",
    "not_found",
    "raise_usage",
    "rejected_token_error",
    "run_hint",
    # input
    "AbsolutePath",
    "StdinOption",
    "existing_file",
    "parse_json_pairs",
    "parse_kv_pairs",
    "read_stdin",
    "read_stdin_text",
    "read_structured_file",
    "resolve_each",
    # batch and concurrency
    "BatchOutcome",
    "DryRunOption",
    "LimitOption",
    "ParallelOption",
    "YesOption",
    "batch_apply",
    "bounded_map",
    "clamp_parallel",
    # http
    "BoundSecret",
    "Credential",
    "CredentialKey",
    "HttpClient",
    "HttpFactory",
    "HttpError",
    "HttpSettings",
    "HttpStatusError",
    "HttpTransportError",
    "NeedsSetup",
    "NoCredential",
    "RetryPolicy",
    "Suggest",
    "TlsVerify",
    "TokenUrl",
    "connected_client",
    "paginate_link",
    "paginate_offset",
    "paginate_pages",
    "resolve_verify",
    # git and filesystem
    "GitCommandError",
    "GitResult",
    "atomic_write",
    "checkout_root",
    "file_lock",
    "find_program",
    "git_toplevel",
    "nearest_pyproject",
    "run_git",
    "safe_path_segment",
    "same_origin",
    "yaml_mapping_indent",
    # projects
    "HolderState",
    "HolderView",
    "ItemStore",
    "LetGo",
    "MoveAsk",
    "Project",
    "Refused",
    "ReleaseAsk",
    "UnknownHolder",
    "committed_path",
    "current_project",
    "known_projects",
    "project_at",
    # the home
    "home",
    "plugin_path",
    # caches and stores
    "Cache",
    "Dir",
    "Filled",
    "Freshness",
    "FreshnessRecord",
    "Lock",
    "NeverRefreshed",
    "RefHolder",
    "RefTarget",
    "Served",
    "Store",
    "code_version",
    "dir_bytes",
    "holders",
    "installed_code",
    "refresh_option",
    "setting_guard",
    # data files
    "Absent",
    "DataFile",
    "DataRead",
    "Found",
    "OtherVersion",
    "Unreadable",
    "read_data_file",
    "write_data_file",
    # prompts and ui
    "NO_PROGRESS",
    "PickCatalog",
    "PickItem",
    "PickRequest",
    "PickResult",
    "PickSetting",
    "Picked",
    "ProgressHandle",
    "PromptChoice",
    "Terminal",
    "run_editor",
    # doctor checks
    "executable_check",
    "online_check",
    # conventions
    "DEPRECATED_OPTIONS",
    "Deprecated",
    "DeprecatedBehaviour",
    "ExitCode",
    "Renamed",
    "Retired",
    "UntapedDeprecationWarning",
    "ago_text",
    "deprecated",
    "deprecated_alias",
    "deprecated_behaviour",
    "deprecated_parameters",
    "experimental",
    "plural",
    "q",
    "retired_values",
    "shown_path",
    "size_text",
    "writes",
    # screens (experimental)
    "Activate",
    "Back",
    "Binding",
    "Button",
    "Buttons",
    "Cancel",
    "Check",
    "Cmd",
    "CmdError",
    "Cycle",
    "Field",
    "Footer",
    "Form",
    "Frame",
    "Help",
    "Interrupt",
    "ListItem",
    "MultiList",
    "NextField",
    "NumberInput",
    "Panes",
    "Paste",
    "PathInput",
    "Pressed",
    "PrevField",
    "Quit",
    "Resize",
    "Screen",
    "SearchList",
    "SecretInput",
    "Select",
    "SingleList",
    "Submit",
    "Submitted",
    "Tab",
    "Tabs",
    "Tags",
    "TextInput",
    "Tree",
    "TreeRow",
    "Viewport",
]


if not _typing.TYPE_CHECKING:
    # Hidden from the type checker, which resolves the lazy names from the
    # imports above: a module-level ``__getattr__`` would type every other
    # attribute (a plugin's typo) as ``object`` instead of an error.

    def __getattr__(name: str) -> object:
        """Resolve a lazy name on first use and keep it, so later lookups are plain."""
        module = _LAZY_EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(_importlib.import_module(module), name)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*globals(), *_LAZY_EXPORTS})
