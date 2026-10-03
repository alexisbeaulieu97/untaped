"""Tests for the stable provider surface (spec §2)."""

from __future__ import annotations

import pytest

import untaped.sdk as sdk

SURFACE_GROUPS: dict[str, tuple[str, ...]] = {
    "composition": (
        "ApplicationSpec",
        "CapabilityContext",
        "CapabilityProvider",
        "CapabilitySpec",
        "DoctorCheck",
        "DoctorResult",
        "SkillAsset",
        "create_app",
    ),
    "settings and state": (
        "AppContext",
        "StateCollection",
        "StateMap",
        "app_context",
        "get_config_section",
    ),
    "output and records": (
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
    ),
    "errors": (
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
    ),
    "input": (
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
    ),
    "batch and concurrency": (
        "BatchOutcome",
        "DryRunOption",
        "LimitOption",
        "ParallelOption",
        "YesOption",
        "batch_apply",
        "bounded_map",
        "clamp_parallel",
    ),
    "http": (
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
    ),
    "git and filesystem": (
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
    ),
    "prompts and ui": (
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
    ),
    "doctor checks": ("connection_check", "executable_check", "online_check"),
    "conventions": ("ExitCode", "deprecated_alias", "plural", "q", "writes"),
}
EXPECTED_ALL = [name for group in SURFACE_GROUPS.values() for name in group]


def test_all_is_the_topic_groups_in_order() -> None:
    assert sdk.__all__ == EXPECTED_ALL
    assert len(set(EXPECTED_ALL)) == len(EXPECTED_ALL)


def test_removed_names_are_gone() -> None:
    for name in ("CAPABILITY_API_VERSION", "get_core_settings"):
        assert name not in sdk.__all__
        assert not hasattr(sdk, name)


def test_no_extra_module_level_names_leak() -> None:
    """Nothing beyond ``__all__`` is public (retired and registry names stay out)."""
    public = {name for name in dir(sdk) if not name.startswith("_")}
    assert public == set(sdk.__all__) | {"annotations"}


@pytest.mark.parametrize("name", EXPECTED_ALL)
def test_every_name_is_a_reexport_of_its_core_module(name: str) -> None:
    """The SDK module only re-exports; nothing is (re)defined there."""
    assert getattr(getattr(sdk, name), "__module__", None) != sdk.__name__


def test_the_sdk_module_is_untaped_sdk_and_the_old_name_is_gone() -> None:
    import importlib

    mod = importlib.import_module("untaped.sdk")
    assert "UntapedError" in mod.__all__
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("untaped.capability" + "_api")
