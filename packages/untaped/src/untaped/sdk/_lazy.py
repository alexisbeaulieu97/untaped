"""A group's names, each loaded from its home module on first use.

A group's ``__init__`` lists its names but imports none of them: the
modules inside a group import one another, and the runtime imports some of
them, so a group that loaded every member on import would load the run with
the first of its modules anyone touched. :func:`lazy_names` gives the group
its module ``__getattr__`` and ``__dir__`` (PEP 562), and refuses a table
that disagrees with the group's ``__all__``, so the two lists cannot drift.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, MutableMapping, Sequence


def lazy_names(
    namespace: MutableMapping[str, object], homes: Mapping[str, Sequence[str]]
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """``__getattr__`` and ``__dir__`` for the group whose globals are ``namespace``.

    ``homes`` maps each module of the group (its name under the group, or a
    whole dotted path for a home outside it) to the names it is the home of.
    Every name of ``namespace["__all__"]`` is in exactly one module's list,
    and nothing else is.
    """
    package = str(namespace["__name__"])
    home = {
        name: module if module.startswith("untaped.") else f"{package}.{module}"
        for module, names in homes.items()
        for name in names
    }
    listed = list(namespace["__all__"])  # type: ignore[call-overload]
    if sorted(home) != sorted(listed) or len(home) != sum(map(len, homes.values())):
        raise ImportError(f"{package}: its __all__ and the modules its names live in disagree")

    def __getattr__(name: str) -> object:
        if name not in home:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(home[name]), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *home})

    return __getattr__, __dir__


__all__ = ["lazy_names"]
