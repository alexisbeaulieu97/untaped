"""The shell's own profile settings (the ``shell`` section) and alias rules.

:class:`ShellProfileSettings` is the root app's profile model;
``aliases`` maps an alias name (:data:`ALIAS_NAME`) to the argv it stands
for. Kept free of CLI imports so composition and the ``alias`` commands
share one validation.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from untaped.messages import q

#: An alias name: lowercase letters, digits and dashes, like a command name.
ALIAS_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def alias_name_error(name: str) -> str | None:
    """Why ``name`` is not a valid alias name, or ``None`` when it is."""
    if ALIAS_NAME.match(name):
        return None
    return f"alias name must be lowercase letters, digits and dashes: {q(name)}"


def check_aliases(value: dict[str, list[str]]) -> dict[str, list[str]]:
    """Validate an ``aliases`` mapping (names and non-empty argv)."""
    for name, argv in value.items():
        error = alias_name_error(name)
        if error is not None:
            raise ValueError(error)
        if not argv:
            raise ValueError(f"alias {q(name)} has no command")
    return value


class ShellProfileSettings(BaseModel):
    """Shell-level profile-scoped settings (the ``shell`` section)."""

    deprecated_settings: ClassVar[dict[str, str]] = {"aliases": "use a shell alias or function"}

    aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Command aliases: `untaped NAME [ARGS…]` runs the argv stored under NAME. "
        "Managed by `alias` commands. Deprecated: use a shell alias or function.",
    )

    @field_validator("aliases")
    @classmethod
    def _valid_aliases(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return check_aliases(value)


__all__ = ["ALIAS_NAME", "ShellProfileSettings", "alias_name_error", "check_aliases"]
