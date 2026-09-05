"""Stable import surface for capability provider authors (spec §2).

The v1 composition set (eight names) plus the sixteen supported helpers.
Provider packages MUST obtain helpers only via this module.
"""

from __future__ import annotations

from untaped.app_context import app_context
from untaped.batch import finish
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

__all__ = [
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
