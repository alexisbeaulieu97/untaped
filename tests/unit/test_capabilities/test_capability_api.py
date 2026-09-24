"""Tests for the stable provider surface (spec §2)."""

from __future__ import annotations

import pytest

import untaped.capability_api as capi
from untaped import api as sdk_api

EXPECTED_ALL = [
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
    "AbsolutePath",
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
    "TokenCommand",
    "TokenSources",
    "connection_check",
    "executable_check",
]


def test_all_contains_exact_surface() -> None:
    assert capi.__all__ == EXPECTED_ALL
    assert capi.CAPABILITY_API_VERSION == 1.1


def test_no_extra_module_level_names_leak() -> None:
    """Nothing beyond ``__all__`` is public (retired and registry names stay out)."""
    public = {name for name in dir(capi) if not name.startswith("_")}
    assert public == set(capi.__all__) | {"annotations"}


@pytest.mark.parametrize("name", EXPECTED_ALL)
def test_every_name_is_a_reexport_of_its_core_module(name: str) -> None:
    """The SDK module only re-exports; nothing is (re)defined there."""
    assert getattr(getattr(capi, name), "__module__", None) != capi.__name__


def test_sdk_api_does_not_expose_capability_composition_types() -> None:
    assert "SkillAsset" not in sdk_api.__all__
    assert "ToolSpec" not in sdk_api.__all__
    assert "capability_api" not in sdk_api.__all__
