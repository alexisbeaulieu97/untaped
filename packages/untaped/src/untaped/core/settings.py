"""Core's settings and what it keeps, light to import: read at every start.

:class:`CoreSettings` is ``Core``'s settings model: the ``untaped`` section,
``http``, ``ui`` and ``skills`` nested in it (``untaped.http.timeout_seconds``,
``UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS``). :data:`DECLARATION` is the composed
declaration of every plugin class, core's own included: a version-only
cache keyed by the whole install's code, so a start reads it instead of
importing a plugin class, which imports every command. Its files are under
``~/.untaped/plugins/untaped/cached/declaration/``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from untaped.runtime.declaration import KeptDeclaration
from untaped.runtime.settings import HttpSettings, SkillsSettings
from untaped.runtime.theme import UiSettings
from untaped.sdk._home import CORE
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.installed_code import installed_code

#: Core's name as a plugin, which the SDK reserves: its folder is ``~/.untaped/plugins/untaped/``.
NAME = CORE

#: Core's ``declaration``: every plugin class this install composes, kept until any of its
#: code changes. Composition reads it before any run exists, while core's class is what it
#: is composing, so it is bound where it is declared. ``Core.declaration`` holds it.
DECLARATION = Cache(
    label="composed plugins",
    model=KeptDeclaration,
    version=installed_code(),
    max_age=None,
    split="none",
)._bound_to(NAME, "declaration")


class CoreSettings(BaseModel):
    """untaped's own settings (the ``untaped`` section)."""

    http: HttpSettings = Field(
        default_factory=HttpSettings, description="How untaped reaches the network."
    )
    ui: UiSettings = Field(
        default_factory=UiSettings, description="How untaped presents at a terminal."
    )
    skills: SkillsSettings = Field(
        default_factory=SkillsSettings, description="How installed skills are kept current."
    )


__all__ = ["DECLARATION", "NAME", "CoreSettings"]
