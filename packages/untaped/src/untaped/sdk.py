"""The untaped SDK: the one module plugin code (first- or third-party) imports from core.

Every plugin — first-party or third-party — imports untaped helpers
from this module only. It carries the composition set and the supported runtime helpers (output,
errors and exit codes, settings, HTTP, git, stdin/pipe, files and locks, state, UI, batch,
concurrency, shared options, message wording, record bases, setting marks, token sources,
caches, stores and doctor-check factories).
Additions are backwards compatible; removals or signature breaks are a major release.

The screen names (``Screen``, ``Cmd``, ``Key``, the components such as ``TextInput``,
...) are exported lazily, on first use, so ``import untaped.sdk``
loads no screen code and no prompt_toolkit and stays within the startup import
budget; they are experimental (``docs/versioning.md``).
"""

from __future__ import annotations

import importlib as _importlib
import typing as _typing

from untaped.app_context import AppContext, app_context
from untaped.auth import TokenCommand, TokenSources
from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.cache import (
    Cache,
    Filled,
    Freshness,
    FreshnessRecord,
    NeverRefreshed,
    Served,
    code_version,
    refresh_option,
)

# report_error and report_row_errors are deprecated; the SDK exports them until their removal.
from untaped.cli import (  # type: ignore[deprecated]
    ColumnsOption,
    DryRunOption,
    FormatOption,
    LimitOption,
    ParallelOption,
    StdinOption,
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
    report_error,
    report_errors,
    report_row_errors,
    resolve_each,
    writes,
)
from untaped.concurrency import bounded_map
from untaped.deprecated_keys import Renamed, Retired, retired_values
from untaped.diff import unified_diff_text
from untaped.doctor_checks import connection_check, executable_check, online_check
from untaped.editor import run_editor
from untaped.errors import (
    ConfigError,
    ErrorCategory,
    ExitCode,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    OperationCancelledError,
    UntapedError,
    UsageError,
    attribution,
    first_validation_error,
    most_severe,
)
from untaped.fs import atomic_write, file_lock, read_structured_file
from untaped.git import (
    GitCommandError,
    GitResult,
    git_toplevel,
    run_git,
    safe_path_segment,
)
from untaped.http import (
    HttpClient,
    RetryPolicy,
    connected_client,
    paginate_link,
    paginate_offset,
    paginate_pages,
    rejected_token_error,
    resolve_verify,
    same_origin,
)
from untaped.messages import (
    hint,
    not_found,
    plural,
    q,
    run_hint,
    shown_path,
    size_text,
    summary,
)
from untaped.migrations import (
    delete_migration,
    dir_bytes,
    move_migration,
    old_dirs,
    other_layouts_migration,
    setting_guard,
    unsafe_dir,
)
from untaped.output import (
    Debug,
    Diagnostic,
    Diagnostics,
    Failure,
    Message,
    Progress,
    StaleData,
)
from untaped.output.diagnostic import NOT_COUNTED
from untaped.output.progress import NO_PROGRESS, ProgressHandle
from untaped.picker import PickCatalog, Picked, PickItem, PickRequest, PickResult, PickSetting
from untaped.pipe import PipeEnvelope, is_envelope_line, parse_envelope_line
from untaped.plugin_records import Plugin, installed_plugins
from untaped.plugins.registry import (
    DirMigration,
    DoctorCheck,
    DoctorResult,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    PluginSpec,
    SkillAsset,
    plugin_dir,
)
from untaped.prompts import PromptChoice
from untaped.records import (
    AbsolutePath,
    CheckRecord,
    Issued,
    OutcomeRecord,
    Record,
    Source,
    TableGlyph,
    TargetRecord,
    UtcTimestamp,
    kind_of,
    record_model,
)
from untaped.refs import RefHolder, RefTarget, holders
from untaped.setting_marks import NeedsSetup, Secret, Suggest
from untaped.settings import HttpSettings, get_config_section
from untaped.skills import (  # type: ignore[deprecated]
    checkout_root,
    nearest_pyproject,
    project_root,
)
from untaped.stability import (
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
from untaped.state import StateCollection, StateMap
from untaped.stdin import (
    StdinInput,
    read_identifiers,
    read_stdin,
    read_stdin_input,
    resolve_text_input,
)
from untaped.store import Store
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context
from untaped.yaml_roundtrip import yaml_mapping_indent

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
        Key,
        NextField,
        Paste,
        PrevField,
        Quit,
        Resize,
        Screen,
        Submit,
    )

#: Screen names by the module that defines them, resolved on first access (PEP 562).
_SCREEN_MODULES: dict[str, tuple[str, ...]] = {
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
        "Key",
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
_SCREEN_EXPORTS: dict[str, str] = {
    name: module for module, names in _SCREEN_MODULES.items() for name in names
}

__all__ = [  # noqa: RUF022 — grouped by topic; order pinned by test_all_is_the_topic_groups_in_order
    # composition
    "PluginContext",
    "PluginSpec",
    "Plugin",
    "installed_plugins",
    "DoctorCheck",
    "DoctorResult",
    "DirMigration",
    "MigrationOptions",
    "MigrationOutcome",
    "MigrationRow",
    "SkillAsset",
    "create_app",
    "delete_migration",
    "dir_bytes",
    "move_migration",
    "old_dirs",
    "other_layouts_migration",
    "plugin_dir",
    "setting_guard",
    "unsafe_dir",
    # settings and state
    "AppContext",
    "StateCollection",
    "StateMap",
    "app_context",
    "get_config_section",
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
    "PipeEnvelope",
    "Progress",
    "Record",
    "Source",
    "StaleData",
    "TableGlyph",
    "TargetRecord",
    "UtcTimestamp",
    "check_columns",
    "echo",
    "emit",
    "emit_document",
    "finish",
    "is_envelope_line",
    "kind_of",
    "parse_envelope_line",
    "record_model",
    "render_rows",
    "summary",
    "unified_diff_text",
    # errors
    "ConfigError",
    "ErrorCategory",
    "Failure",
    "OperationCancelledError",
    "UntapedError",
    "UsageError",
    "attribution",
    "first_validation_error",
    "hint",
    "most_severe",
    "not_found",
    "raise_usage",
    "rejected_token_error",
    "report_error",
    "report_errors",
    "report_row_errors",
    "run_hint",
    # input
    "AbsolutePath",
    "StdinInput",
    "StdinOption",
    "existing_file",
    "parse_json_pairs",
    "parse_kv_pairs",
    "read_identifiers",
    "read_stdin",
    "read_stdin_input",
    "read_structured_file",
    "resolve_each",
    "resolve_text_input",
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
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "HttpStatusError",
    "HttpTransportError",
    "NeedsSetup",
    "RetryPolicy",
    "Secret",
    "Suggest",
    "TokenCommand",
    "TokenSources",
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
    "git_toplevel",
    "nearest_pyproject",
    "project_root",
    "run_git",
    "safe_path_segment",
    "same_origin",
    "yaml_mapping_indent",
    # caches and stores
    "Cache",
    "Filled",
    "Freshness",
    "FreshnessRecord",
    "NeverRefreshed",
    "RefHolder",
    "RefTarget",
    "Served",
    "Store",
    "code_version",
    "holders",
    "refresh_option",
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
    "UiContext",
    "run_editor",
    "ui_context",
    # doctor checks
    "connection_check",
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
    "Key",
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
    # Hidden from the type checker, which resolves the screen names from the
    # imports above: a module-level ``__getattr__`` would type every other
    # attribute (a plugin's typo) as ``object`` instead of an error.

    def __getattr__(name: str) -> object:
        """Resolve a screen name on first use and keep it, so later lookups are plain."""
        module = _SCREEN_EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(_importlib.import_module(module), name)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*globals(), *_SCREEN_EXPORTS})
