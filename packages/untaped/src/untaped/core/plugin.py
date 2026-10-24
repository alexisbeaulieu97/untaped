"""Core as a plugin of itself: the class that holds what untaped keeps for its own use.

Today it holds one block, the composed declaration of every 12.0 plugin
class: a version-only cache keyed by the whole install's code, so a start
reads it instead of importing a plugin class, which imports every command
(plugin-interface.md, decision 11). Its files are under
``~/.untaped/plugins/untaped/cached/declaration/``. Core's commands and
settings join the class when core moves onto the plugin shape; until then
composition reads the cache spelled (:func:`untaped.runtime.registry.compose`).
"""

from __future__ import annotations

from untaped.runtime.declaration import Declaration
from untaped.sdk.plugin import Plugin
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.installed_code import installed_code

#: Core's name as a plugin: its folder is ``~/.untaped/plugins/untaped/``.
NAME = "untaped"

#: Every 12.0 plugin class this install composes, kept until any of its code changes.
DECLARATION = Cache(
    label="composed plugins",
    model=Declaration,
    version=installed_code(),
    max_age=None,
    split="none",
)


class Core(Plugin):
    """untaped's own commands: config, profiles, projects, skills, doctor, setup, auth, plugins."""

    declaration = DECLARATION


__all__ = ["DECLARATION", "NAME", "Core"]
