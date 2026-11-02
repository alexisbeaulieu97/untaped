"""Tests for the stable provider surface."""

from __future__ import annotations

from types import ModuleType

import pytest

import untaped.sdk as sdk

SURFACE_GROUPS: dict[str, tuple[str, ...]] = {
    "the plugin and its commands": (
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
    ),
    "composition": (
        "InstalledPlugin",
        "installed_plugins",
    ),
    "output and records": (
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
    ),
    "errors": (
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
    ),
    "input": (
        "AbsolutePath",
        "read_stdin_text",
    ),
    "concurrency": ("bounded_map",),
    "http": (
        "HttpError",
        "HttpStatusError",
        "HttpTransportError",
    ),
    "git and filesystem": ("yaml_mapping_indent",),
    "the home": ("home",),
    "caches and stores": ("installed_code",),
    "prompts and ui": ("Terminal",),
    "conventions": (
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
    ),
}
EXPECTED_ALL = [name for group in SURFACE_GROUPS.values() for name in group]


def test_all_is_the_topic_groups_in_order() -> None:
    assert sdk.__all__ == EXPECTED_ALL
    assert len(set(EXPECTED_ALL)) == len(EXPECTED_ALL)


def test_no_extra_module_level_names_leak() -> None:
    """Nothing beyond ``__all__`` and the groups is public (retired and registry names stay out)."""
    public = {
        name
        for name in dir(sdk)
        if not name.startswith("_") and not isinstance(getattr(sdk, name), ModuleType)
    }
    assert public == set(sdk.__all__) | {"annotations"}


@pytest.mark.parametrize("name", EXPECTED_ALL)
def test_every_name_is_a_reexport_of_its_core_module(name: str) -> None:
    """The SDK module only re-exports; nothing is (re)defined there."""
    assert getattr(getattr(sdk, name), "__module__", None) != sdk.__name__


def test_an_unknown_attribute_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'Nope'"):
        sdk.Nope  # noqa: B018


def test_screens_live_outside_the_sdk() -> None:
    assert not {"Screen", "Cmd", "Quit", "Field"} & set(dir(sdk))
