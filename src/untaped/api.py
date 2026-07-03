"""The untaped SDK surface.

Tools import from this module (``from untaped.api import ...``) instead of
reaching into SDK internals. Names listed in ``__all__`` are the SDK contract:
additions are backwards-compatible; removing a name or changing its behaviour
is a major SDK version event. Internal modules stay free to reorganize as long
as this surface keeps resolving. ``untaped`` (the package root) re-exports this
exact surface, so ``from untaped import X`` and ``from untaped.api import X``
are equivalent.
"""

from __future__ import annotations

from untaped.app_context import AppContext, app_context
from untaped.batch import BatchOutcome, batch_apply
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    clamp_parallel,
    create_app,
    echo,
    emit,
    existing_directory,
    existing_file,
    parse_kv_pairs,
    raise_usage,
    render_rows,
    report_errors,
    resolve_each,
)
from untaped.config_file import ensure_config, mutate_tool_state, read_tool_state
from untaped.errors import (
    ConfigError,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    UntapedError,
    first_validation_error,
)
from untaped.http import (
    HttpClient,
    RetryPolicy,
    connected_client,
    paginate_offset,
    paginate_pages,
    resolve_verify,
)
from untaped.pipe import PipeEnvelope, common_kind, is_envelope_line, parse_envelope_line
from untaped.progress import ProgressHandle
from untaped.prompts import PromptChoice
from untaped.render import OutputFormat
from untaped.run import build_tool_app, run_tool
from untaped.settings import (
    HttpSettings,
    get_config_section,
    get_core_settings,
    get_settings,
)
from untaped.stdin import read_identifiers, read_records, read_stdin
from untaped.theme import ThemeSpec
from untaped.tool import SkillAsset, ToolSpec, register_tool
from untaped.ui import UiContext, ui_context


def invalidate_settings_cache() -> None:
    """Drop the cached settings instance so the next read re-resolves.

    Root-option handlers (e.g. the built-in ``--profile``) call this after
    changing process state that feeds settings resolution.
    """
    get_settings.cache_clear()


__all__ = [
    "AppContext",
    "BatchOutcome",
    "ColumnsOption",
    "ConfigError",
    "FormatOption",
    "HttpClient",
    "HttpError",
    "HttpSettings",
    "HttpStatusError",
    "HttpTransportError",
    "OutputFormat",
    "PipeEnvelope",
    "ProgressHandle",
    "PromptChoice",
    "RetryPolicy",
    "SkillAsset",
    "ThemeSpec",
    "ToolSpec",
    "UiContext",
    "UntapedError",
    "app_context",
    "batch_apply",
    "build_tool_app",
    "clamp_parallel",
    "common_kind",
    "connected_client",
    "create_app",
    "echo",
    "emit",
    "ensure_config",
    "existing_directory",
    "existing_file",
    "first_validation_error",
    "get_config_section",
    "get_core_settings",
    "get_settings",
    "invalidate_settings_cache",
    "is_envelope_line",
    "mutate_tool_state",
    "paginate_offset",
    "paginate_pages",
    "parse_envelope_line",
    "parse_kv_pairs",
    "raise_usage",
    "read_identifiers",
    "read_records",
    "read_stdin",
    "read_tool_state",
    "register_tool",
    "render_rows",
    "report_errors",
    "resolve_each",
    "resolve_verify",
    "run_tool",
    "ui_context",
]
