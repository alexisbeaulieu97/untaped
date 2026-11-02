"""The untaped SDK: what plugin code (first- or third-party) imports from core.

The public surface is the names ``untaped.sdk`` exports and those of its
groups (``storage``, ``network``, ``health``, ``git``, ``projects``,
``prompt``, ``contracts`` and ``testing``); a ``_``-prefixed module or name
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

    def _resolve(name: str) -> object:
        """``name``'s object from the table, an old name's replacement included; kept nowhere."""
        exports = _importlib.import_module("untaped.sdk._exports")
        if name in exports.__all__ or name == "__all__":
            return getattr(exports, name)
        mark = None if name.startswith("__") else exports.OLD_NAMES.get(name)
        if mark is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return mark.replacement

    globals()["__untaped_resolve__"] = _resolve  # the name _lazy.RESOLVE_ATTR reads

    def __getattr__(name: str) -> object:
        """Resolve a name from the table on first use and keep it.

        An old name (``_exports.OLD_NAMES``) or a name marked deprecated
        warns at the lookup and is never kept, so every lookup warns
        (:func:`untaped.sdk._lazy.looked_up`).
        """
        value = _resolve(name)
        exports = _importlib.import_module("untaped.sdk._exports")
        if name == "__all__":
            globals()[name] = value
            return value
        from untaped.sdk._lazy import looked_up  # noqa: PLC0415

        old = None if name in exports.__all__ else exports.OLD_NAMES.get(name)
        return looked_up(globals(), __name__, name, value, old)

    def __dir__() -> list[str]:
        exports = _importlib.import_module("untaped.sdk._exports")
        return sorted({*globals(), *exports.__all__})
