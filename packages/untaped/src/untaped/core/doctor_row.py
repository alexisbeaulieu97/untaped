"""What every ``untaped doctor`` row is built from: its statuses, its shape and its scope.

:func:`doctor_row` builds one row as the dict a ``untaped.doctor_check``
record validates; :class:`SectionScope` is one section's settings model,
checks and state rows; :func:`placeholders` names the ``<NAME>`` values a
fix still needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from untaped.sdk.health.doctor import DoctorCheck
from untaped.sdk.storage.state import ProjectStateRows, StateRows

PASS = "pass"
FAIL = "fail"
WARN = "warn"
STATUSES = (PASS, WARN, FAIL)

PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")


@dataclass(frozen=True)
class SectionScope:
    """One section's health scope: models plus the check bodies owning it."""

    plugin: str
    section: str
    settings_model: type[BaseModel] | None
    checks: tuple[DoctorCheck, ...]
    rows: tuple[StateRows[Any] | ProjectStateRows[Any], ...] = ()
    """The section's typed rows, each read by its own rules."""


def doctor_row(
    check: str,
    plugin: str,
    status: str,
    title: str,
    detail: str,
    fix: list[str] | None = None,
    *,
    automatic: bool = False,
) -> dict[str, object]:
    return {
        "check": check,
        "plugin": plugin,
        "status": status,
        "title": title,
        "detail": detail,
        "fix": fix,
        "automatic": bool(automatic) and fix is not None,
    }


def placeholders(argv: list[str]) -> list[str]:
    """The ``<NAME>`` values ``argv`` still needs, in order."""
    return [found for arg in argv for found in PLACEHOLDER.findall(arg)]
