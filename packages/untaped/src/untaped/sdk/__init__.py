"""The untaped SDK: what plugin code (first- or third-party) imports from core.

The public surface is the names ``untaped.sdk`` exports and those of its
groups (``storage``, ``network``, ``health``, ``git``, ``process``,
``projects``, ``prompt``, ``contracts`` and ``testing``); a ``_``-prefixed module or name
is never public. Each name has one spelling: a group's names are imported
from the group, and the root exports none of them. Additions are backwards
compatible; removals or signature breaks are a major release.

Importing the package loads nothing: each name resolves on first use from
:mod:`untaped.sdk._exports`, so a group's module never pays for the rest.
"""

from __future__ import annotations

import importlib as _importlib
import typing as _typing

if _typing.TYPE_CHECKING:
    from untaped.sdk._exports import *  # noqa: F403
    from untaped.sdk._exports import __all__ as __all__
else:

    def _old_names() -> object:
        """The root's old names (``_exports.OLD_NAMES``), for the surface walker."""
        return _importlib.import_module("untaped.sdk._exports").OLD_NAMES

    globals()["__untaped_old_names__"] = _old_names  # the name _lazy.OLD_NAMES_ATTR reads

    def __getattr__(name: str) -> object:
        """Resolve a name from the table on first use and keep it.

        An old name (``_exports.OLD_NAMES``) resolves to its replacement on
        every lookup, with a warning, and is never kept.
        """
        exports = _importlib.import_module("untaped.sdk._exports")
        if name != "__all__" and name not in exports.__all__:
            mark = None if name.startswith("__") else exports.OLD_NAMES.get(name)
            if mark is None:
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
            from untaped.sdk._lazy import resolve_old  # noqa: PLC0415

            return resolve_old(__name__, name, mark)
        value = getattr(exports, name)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        exports = _importlib.import_module("untaped.sdk._exports")
        return sorted({*globals(), *exports.__all__})
