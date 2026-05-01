"""Pure formatting helpers for ``untaped config list`` table cells."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr
from untaped_core.config_schema import FieldDescriptor


def display_value(descriptor: FieldDescriptor, value: Any, *, reveal_secrets: bool) -> str:
    """Format a setting value for table display."""
    if value is None:
        return "—"
    if descriptor.is_secret and not reveal_secrets:
        return "***"
    if isinstance(value, SecretStr):
        return value.get_secret_value() if reveal_secrets else "***"
    return str(value)


def display_default(descriptor: FieldDescriptor) -> str:
    if not descriptor.has_default or descriptor.default is None:
        return "—"
    if isinstance(descriptor.default, SecretStr):
        return descriptor.default.get_secret_value()
    return str(descriptor.default)
