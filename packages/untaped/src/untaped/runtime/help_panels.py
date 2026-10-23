"""The help panels core owns, and the order every ``--help`` lists them in.

Core places root commands and global options; a plugin never names one of
these panels for its own commands or options (``reserved-panel``), and keys its
own panels below :data:`PLUGIN_PANEL_KEY_LIMIT` (``unkeyed-panel``), so the
order below holds on every screen:

- Commands: cyclopts' unkeyed default panel, which sorts before any keyed one;
- Plugins (50): every unmarked plugin's root command;
- Experimental (100) and Deprecated (200): marked commands, and Deprecated
  options (250): a command's options marked ``deprecated(...)``, all from
  :mod:`untaped.sdk.marks`, which keeps them because the deprecated panels'
  visibility is the ``--deprecated`` flag's;
- Global options (300): the root options (``--profile``, ``--verbose``,
  ``--quiet``, ``--deprecated``) on every screen, plus root's own ``--help``,
  ``--version`` and ``--install-completion``.
"""

from __future__ import annotations

from cyclopts import Group

from untaped.sdk.marks import DEPRECATED_GROUP, DEPRECATED_OPTIONS, EXPERIMENTAL_GROUP

PLUGINS_GROUP = Group("Plugins", sort_key=50)
GLOBAL_OPTIONS_GROUP = Group("Global options", sort_key=300)
CORE_PANELS = (
    PLUGINS_GROUP,
    EXPERIMENTAL_GROUP,
    DEPRECATED_GROUP,
    DEPRECATED_OPTIONS,
    GLOBAL_OPTIONS_GROUP,
)
RESERVED_PANELS = frozenset(group.name for group in CORE_PANELS)
#: A plugin's own panels take sort keys below this, so they list before core's
#: keyed panels and Global options stays last.
PLUGIN_PANEL_KEY_LIMIT = 100


def is_core_panel(group: object) -> bool:
    """Whether ``group`` is one of core's panels (by identity, not by name)."""
    return any(group is panel for panel in CORE_PANELS)
