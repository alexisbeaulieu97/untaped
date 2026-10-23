"""The untaped SDK: what plugin code (first- or third-party) imports from core.

Everything importable from ``untaped.sdk`` and its groups is public; a
``_``-prefixed module or name is not. The groups are subpackages:
``storage``, ``contracts``, ``network``, ``health``, ``git``, ``projects``,
``prompt`` and ``testing``. Additions are backwards compatible; removals or
signature breaks are a major release.

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

    def __getattr__(name: str) -> object:
        """Resolve a name from the table on first use and keep it."""
        exports = _importlib.import_module("untaped.sdk._exports")
        if name != "__all__" and name not in exports.__all__:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(exports, name)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        exports = _importlib.import_module("untaped.sdk._exports")
        return sorted({*globals(), *exports.__all__})
