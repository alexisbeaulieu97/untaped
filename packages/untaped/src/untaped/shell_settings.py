"""The shell's own profile settings (the ``shell`` section).

:class:`ShellProfileSettings` is the root app's settings model. It holds no
setting today; it remains the section's home, so ``doctor`` and
``config migrate`` still know its retired keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from untaped.deprecated_keys import Retired


class ShellProfileSettings(BaseModel):
    """Shell-level profile-scoped settings (the ``shell`` section)."""

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "aliases": Retired(note="deleted in 11.0; use a shell alias or function"),
    }


__all__ = ["ShellProfileSettings"]
