"""The single public SDK surface for capability code (spec §2).

Every capability — built-in or external provider — imports untaped helpers
from this module only. It carries the composition set, the provider
``CAPABILITY_API_VERSION``, and the supported runtime helpers (output,
errors and exit codes, settings, HTTP, git, stdin/pipe, state, UI, batch,
concurrency, shared options, message wording and record bases).
Additions are backwards compatible; removals or signature breaks require a
major ``CAPABILITY_API_VERSION`` bump.
"""

from __future__ import annotations

from untaped.app_context import AppContext, app_context
from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.capabilities.registry import (
    CAPABILITY_API_VERSION,
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
    report_errors,
    resolve_each,
)
from untaped.concurrency import bounded_map
from untaped.diff import unified_diff_text
from untaped.editor import run_editor
from untaped.errors import (
    ConfigError,
    ExitCode,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    OperationCancelledError,
    UntapedError,
    UsageError,
    first_validation_error,
)
from untaped.fs import atomic_write, read_structured_file
from untaped.git import (
    GitCommandError,
    GitResult,
    git_auth_header,
    run_git,
    safe_cache_path,
    safe_path_segment,
)
from untaped.http import (
    HttpClient,
    RetryPolicy,
    connected_client,
    paginate_link,
    paginate_offset,
    paginate_pages,
    resolve_verify,
)
from untaped.messages import hint, not_found, plural, q, summary
from untaped.pipe import PipeEnvelope, is_envelope_line, parse_envelope_line
from untaped.progress import ProgressHandle
from untaped.prompts import PromptChoice
from untaped.records import CheckRecord, OutcomeRecord, TargetRecord, UtcTimestamp
from untaped.render import OutputFormat
from untaped.settings import HttpSettings, get_config_section, get_core_settings
from untaped.state import StateCollection, StateMap
from untaped.stdin import (
    StdinInput,
    read_identifiers,
    read_records,
    read_stdin,
    read_stdin_input,
    resolve_text_input,
)
from untaped.ui import UiContext, ui_context

__all__ = [  # noqa: RUF022 — grouped composition and helpers; order pinned by test_all_contains_exact_surface
    "ApplicationSpec",
    "CapabilitySpec",
    "CapabilityProvider",
    "CAPABILITY_API_VERSION",
    "SkillAsset",
    "DoctorCheck",
    "DoctorResult",
    "CapabilityContext",
    "ColumnsOption",
    "ConfigError",
    "FormatOption",
    "PipeEnvelope",
    "StateCollection",
    "UiContext",
    "UntapedError",
    "app_context",
    "create_app",
    "echo",
    "emit",
    "finish",
    "first_validation_error",
    "get_config_section",
    "parse_envelope_line",
    "raise_usage",
    "read_identifiers",
    "report_errors",
    "run_editor",
    "GitCommandError",
    "GitResult",
    "git_auth_header",
    "run_git",
    "safe_cache_path",
    "safe_path_segment",
    # Additive helpers folded in from the retired ``untaped.api`` module.
    "AppContext",
    "BatchOutcome",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "HttpStatusError",
    "HttpTransportError",
    "OutputFormat",
    "ProgressHandle",
    "PromptChoice",
    "RetryPolicy",
    "StateMap",
    "atomic_write",
    "batch_apply",
    "bounded_map",
    "clamp_parallel",
    "connected_client",
    "existing_file",
    "get_core_settings",
    "is_envelope_line",
    "paginate_link",
    "paginate_offset",
    "paginate_pages",
    "parse_json_pairs",
    "parse_kv_pairs",
    "read_stdin",
    "read_structured_file",
    "render_rows",
    "resolve_each",
    "resolve_text_input",
    "resolve_verify",
    "ui_context",
    "unified_diff_text",
    # 1.1 UX-convention helpers (docs/conventions.md).
    "CheckRecord",
    "DryRunOption",
    "ExitCode",
    "LimitOption",
    "OperationCancelledError",
    "OutcomeRecord",
    "ParallelOption",
    "StdinInput",
    "StdinOption",
    "TargetRecord",
    "UsageError",
    "UtcTimestamp",
    "YesOption",
    "deprecated_alias",
    "hint",
    "not_found",
    "plural",
    "q",
    "read_records",
    "read_stdin_input",
    "summary",
]
