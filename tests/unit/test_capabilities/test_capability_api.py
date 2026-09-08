"""Tests for the stable provider surface (spec §2)."""

from __future__ import annotations

import sys

import pytest

import untaped.capability_api as capi
from untaped import api as sdk_api
from untaped import batch as batch_mod
from untaped import cli as cli_mod
from untaped import errors as errors_mod
from untaped import settings as settings_mod
from untaped import state as state_mod
from untaped import stdin as stdin_mod
from untaped import ui as ui_mod
from untaped.app_context import app_context
from untaped.batch import finish
from untaped.capabilities import registry
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    emit,
    raise_usage,
    report_errors,
)
from untaped.errors import ConfigError, UntapedError, first_validation_error
from untaped.settings import get_config_section
from untaped.state import StateCollection
from untaped.stdin import read_identifiers
from untaped.ui import UiContext

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
    "raise_usage",
    "read_identifiers",
    "report_errors",
]


def test_all_contains_exactly_eight_plus_sixteen() -> None:
    assert capi.__all__ == EXPECTED_ALL


def test_composition_names_resolve_to_registry() -> None:
    assert capi.ApplicationSpec is registry.ApplicationSpec
    assert capi.CapabilitySpec is registry.CapabilitySpec
    assert capi.CapabilityProvider is registry.CapabilityProvider
    assert capi.CAPABILITY_API_VERSION == registry.CAPABILITY_API_VERSION == 1.0
    assert capi.SkillAsset is registry.SkillAsset
    assert capi.DoctorCheck is registry.DoctorCheck
    assert capi.DoctorResult is registry.DoctorResult
    assert capi.CapabilityContext is registry.CapabilityContext


def test_helpers_resolve_to_canonical_sources() -> None:
    assert capi.ColumnsOption is ColumnsOption
    assert capi.FormatOption is FormatOption
    assert capi.create_app is create_app
    assert capi.echo is echo
    assert capi.emit is emit
    assert capi.raise_usage is raise_usage
    assert capi.report_errors is report_errors
    assert capi.ConfigError is ConfigError
    assert capi.UntapedError is UntapedError
    assert capi.first_validation_error is first_validation_error
    assert capi.StateCollection is StateCollection
    assert capi.UiContext is UiContext
    assert capi.app_context is app_context
    assert capi.finish is finish
    assert capi.get_config_section is get_config_section
    assert capi.read_identifiers is read_identifiers


def test_helpers_match_sdk_modules() -> None:
    assert capi.create_app is cli_mod.create_app
    assert capi.emit is cli_mod.emit
    assert capi.ConfigError is errors_mod.ConfigError
    assert capi.get_config_section is settings_mod.get_config_section
    assert capi.StateCollection is state_mod.StateCollection
    assert capi.read_identifiers is stdin_mod.read_identifiers
    assert capi.UiContext is ui_mod.UiContext
    assert capi.app_context is sys.modules["untaped.app_context"].app_context
    assert capi.finish is batch_mod.finish


def test_retired_composition_names_are_excluded() -> None:
    for name in ("ToolSpec", "register_tool", "build_tool_app", "run_tool"):
        assert name not in capi.__all__
        assert not hasattr(capi, name), name


def test_registry_internals_are_not_exported() -> None:
    for name in (
        "compose",
        "ExternalProvider",
        "CompositionResult",
        "RegisteredCapability",
        "ProviderRef",
        "QuarantineRecord",
        "VALID_REASONS",
        "check_api_range",
        "check_builtin_metadata",
    ):
        assert name not in capi.__all__
        assert not hasattr(capi, name), name


def test_every_all_name_resolves() -> None:
    for name in capi.__all__:
        assert getattr(capi, name) is not None, name


def test_no_extra_module_level_names_leak() -> None:
    public = {name for name in dir(capi) if not name.startswith("_")}
    assert public == set(capi.__all__) | {"annotations"}


def test_sdk_api_does_not_expose_capability_composition_types() -> None:
    assert "SkillAsset" not in sdk_api.__all__
    assert "ToolSpec" not in sdk_api.__all__
    assert "capability_api" not in sdk_api.__all__


@pytest.mark.parametrize("name", EXPECTED_ALL)
def test_all_entries_importable_from_module(name: str) -> None:
    assert name in dir(capi)
