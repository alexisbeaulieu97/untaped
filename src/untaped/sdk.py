"""The untaped SDK: the one module capability code (first- or third-party) imports from core.

Every capability — first-party or third-party — imports untaped helpers
from this module only. It carries the composition set and the supported runtime helpers (output,
errors and exit codes, settings, HTTP, git, stdin/pipe, files and locks, state, UI, batch,
concurrency, shared options, message wording, record bases, token sources
and doctor-check factories).
Additions are backwards compatible; removals or signature breaks are a major release.
"""

from __future__ import annotations

from untaped.app_context import AppContext, app_context
from untaped.auth import TokenCommand, TokenSources
from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.capabilities.registry import (
    ApplicationSpec,
    CapabilityContext,
    CapabilityProvider,
    CapabilitySpec,
    DoctorCheck,
    DoctorResult,
    SkillAsset,
)
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
    deprecated_alias,
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

__all__ = [  # noqa: RUF022 — grouped by topic; order pinned by test_all_is_the_topic_groups_in_order
    # composition
    "ApplicationSpec",
    "CapabilityContext",
    "CapabilityProvider",
    "CapabilitySpec",
    "DoctorCheck",
    "DoctorResult",
    "SkillAsset",
    "create_app",
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
    "deprecated_alias",
    "plural",
    "q",
    "writes",
]
