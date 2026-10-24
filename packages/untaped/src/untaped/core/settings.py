"""Core's settings and what it keeps, light to import: read at every start.

:class:`CoreSettings` is ``Core``'s settings model, empty until core's
settings move under ``untaped:``. :data:`DECLARATION` is the composed
declaration of every plugin class, core's own included: a version-only
cache keyed by the whole install's code, so a start reads it instead of
importing a plugin class, which imports every command (plugin-interface.md,
decision 11). Its files are under
``~/.untaped/plugins/untaped/cached/declaration/``.

:class:`ShellProfileSettings` is the 11.x root app's settings model (the
``shell`` section). It holds no setting; it remains the section's home, so
``doctor`` and ``config migrate`` still know its retired keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from untaped.runtime.declaration import KeptDeclaration
from untaped.sdk.health.deprecated_keys import Retired
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.installed_code import installed_code

#: Core's name as a plugin: its folder is ``~/.untaped/plugins/untaped/``.
NAME = "untaped"

#: Every plugin class this install composes, kept until any of its code changes.
DECLARATION = Cache(
    label="composed plugins",
    model=KeptDeclaration,
    version=installed_code(),
    max_age=None,
    split="none",
)


class CoreSettings(BaseModel):
    """untaped's own settings: none yet as a plugin (``http``, ``ui`` and ``skills`` join)."""


class ShellProfileSettings(BaseModel):
    """Shell-level profile-scoped settings (the ``shell`` section)."""

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "aliases": Retired(note="deleted in 11.0; use a shell alias or function"),
    }


__all__ = ["DECLARATION", "NAME", "CoreSettings", "ShellProfileSettings"]
