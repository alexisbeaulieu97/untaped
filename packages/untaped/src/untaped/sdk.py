"""The untaped SDK: the one module plugin code (first- or third-party) imports from core.

Every plugin — first-party or third-party — imports untaped helpers
from this module only. It carries the composition set and the supported runtime helpers (output,
errors and exit codes, settings, HTTP, git, stdin/pipe, files and locks, state, UI, batch,
concurrency, shared options, message wording, record bases, token sources
and doctor-check factories).
Additions are backwards compatible; removals or signature breaks are a major release.

The screen names (``Screen``, ``Cmd``, ``Key``, the components such as ``TextInput``
and ``field_for``, ...) are exported lazily, on first use, so ``import untaped.sdk``
loads no screen code and no prompt_toolkit and stays within the startup import
budget; they are experimental (``docs/versioning.md``).
"""

from __future__ import annotations

import importlib as _importlib
import typing as _typing

from untaped.app_context import AppContext, app_context
from untaped.auth import TokenCommand, TokenSources
from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    LimitOption,
    ParallelOption,
    StdinOption,
    YesOption,
    clamp_parallel,
    create_app,
    echo,
    emit,
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
from untaped.diagnostics import ErrorInfo, note_failure
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
    git_auth_header,
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
from untaped.messages import hint, not_found, plural, q, summary
from untaped.picker import PickCatalog, Picked, PickItem, PickRequest, PickResult, PickSetting
from untaped.pipe import PipeEnvelope, is_envelope_line, parse_envelope_line
from untaped.plugins.registry import (
    ApplicationSpec,
    DoctorCheck,
    DoctorResult,
    PluginContext,
    PluginProvider,
    PluginSpec,
    SkillAsset,
    plugin_dir,
)
from untaped.progress import ProgressHandle
from untaped.prompts import PromptChoice
from untaped.records import (
    AbsolutePath,
    CheckRecord,
    OutcomeRecord,
    TableGlyph,
    TargetRecord,
    UtcTimestamp,
)
from untaped.repo_cache import (
    RepoCache,
    cache_key,
    cache_origin,
    cache_path,
    list_caches,
    repo_url_parts,
    scoped_auth_header,
)
from untaped.settings import HttpSettings, get_config_section
from untaped.stability import deprecated, deprecated_alias, experimental
from untaped.state import StateCollection, StateMap
from untaped.stdin import (
    StdinInput,
    read_identifiers,
    read_records,
    read_stdin,
    read_stdin_input,
    resolve_text_input,
)
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
    from untaped.screen.components.fields import Field, field_for
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
    "untaped.screen.components.fields": ("Field", "field_for"),
    "untaped.screen.components.form": ("Form", "Submitted"),
    "untaped.screen.components.layout": ("Panes", "Viewport"),
    "untaped.screen.components.lists": ("SearchList", "Tags", "Tree", "TreeRow"),
}
_SCREEN_EXPORTS: dict[str, str] = {
    name: module for module, names in _SCREEN_MODULES.items() for name in names
}

__all__ = [  # noqa: RUF022 — grouped by topic; order pinned by test_all_is_the_topic_groups_in_order
    # composition
    "ApplicationSpec",
    "PluginContext",
    "PluginProvider",
    "PluginSpec",
    "DoctorCheck",
    "DoctorResult",
    "SkillAsset",
    "create_app",
    "plugin_dir",
    # settings and state
    "AppContext",
    "StateCollection",
    "StateMap",
    "app_context",
    "get_config_section",
    # output and records
    "CheckRecord",
    "ColumnsOption",
    "FormatOption",
    "OutcomeRecord",
    "OutputFormat",
    "PipeEnvelope",
    "TableGlyph",
    "TargetRecord",
    "UtcTimestamp",
    "echo",
    "emit",
    "finish",
    "is_envelope_line",
    "parse_envelope_line",
    "read_records",
    "render_rows",
    "summary",
    "unified_diff_text",
    # errors
    "ConfigError",
    "ErrorCategory",
    "ErrorInfo",
    "OperationCancelledError",
    "UntapedError",
    "UsageError",
    "attribution",
    "first_validation_error",
    "hint",
    "most_severe",
    "not_found",
    "note_failure",
    "raise_usage",
    "rejected_token_error",
    "report_error",
    "report_errors",
    "report_row_errors",
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
    "RetryPolicy",
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
    "RepoCache",
    "atomic_write",
    "cache_key",
    "cache_origin",
    "cache_path",
    "file_lock",
    "git_auth_header",
    "git_toplevel",
    "list_caches",
    "repo_url_parts",
    "run_git",
    "safe_path_segment",
    "same_origin",
    "scoped_auth_header",
    "yaml_mapping_indent",
    # prompts and ui
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
    "ExitCode",
    "deprecated",
    "deprecated_alias",
    "experimental",
    "plural",
    "q",
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
    "field_for",
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
