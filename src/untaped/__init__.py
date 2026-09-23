"""The unified untaped application.

The public SDK surface lives in :mod:`untaped.capability_api`. For backwards
compatibility, ``from untaped import X`` still resolves the names of
:mod:`untaped.capability_api` and of the deprecated :mod:`untaped.api` shim
(the root's former re-exports) through a lazy module ``__getattr__``, so
importing the package loads nothing. That forwarding is deprecated and will be
removed with :mod:`untaped.api`; import from :mod:`untaped.capability_api`.
"""

from __future__ import annotations

from importlib.util import find_spec


def __getattr__(name: str) -> object:
    """Forward deprecated root imports to the SDK modules on first access."""
    # Dunders and submodules (``from untaped import bootstrap``) are never
    # forwarded; a missing attribute lets the import system load the submodule.
    if name.startswith("_") or find_spec(f"{__name__}.{name}") is not None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from untaped import api, capability_api  # noqa: PLC0415

    for source in (capability_api, api):
        if name in source.__all__:
            value = getattr(source, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
