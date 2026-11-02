"""A group's names, each loaded from its home module on first use.

A group's ``__init__`` lists its names but imports none of them: the
modules inside a group import one another, and the runtime imports some of
them, so a group that loaded every member on import would load the run with
the first of its modules anyone touched. :func:`lazy_names` gives the group
its module ``__getattr__`` and ``__dir__`` (PEP 562), and refuses a table
that disagrees with the group's ``__all__``, so the two lists cannot drift.

A renamed name keeps its old spelling through an ``old=`` table
(:func:`untaped.sdk.marks.old_names`), built on the first lookup of a name
the group doesn't list: :func:`resolve_old` hands back the new object on
every lookup, never keeping the old name in the group's globals, and warns.
"""

from __future__ import annotations

import functools
import importlib
import types
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.sdk.marks import Deprecated


#: Where a module keeps its old-names table (a function returning it), for the surface walker.
OLD_NAMES_ATTR = "__untaped_old_names__"


def lazy_names(
    namespace: MutableMapping[str, object],
    homes: Mapping[str, Sequence[str]],
    *,
    old: Callable[[], Mapping[str, Deprecated]] | None = None,
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """``__getattr__`` and ``__dir__`` for the group whose globals are ``namespace``.

    ``homes`` maps each module of the group (its name under the group, or a
    whole dotted path for a home outside it) to the names it is the home of.
    Every name of ``namespace["__all__"]`` is in exactly one module's list,
    and nothing else is. ``old`` returns the group's old names
    (:func:`untaped.sdk.marks.old_names`); it runs on the first lookup of a
    name the group doesn't list, so importing the group still loads nothing.
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

    table = functools.cache(old or dict)
    namespace[OLD_NAMES_ATTR] = table

    def __getattr__(name: str) -> object:
        if name not in home:
            mark = table().get(name) if not name.startswith("__") else None
            if mark is None:
                raise AttributeError(f"module {package!r} has no attribute {name!r}")
            return resolve_old(package, name, mark)
        value = getattr(importlib.import_module(home[name]), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *home})

    return __getattr__, __dir__


def old_names_of(module: types.ModuleType) -> Mapping[str, Deprecated]:
    """``module``'s old names, from the table it resolves them from; none when it has none."""
    table = vars(module).get(OLD_NAMES_ATTR)
    return {} if table is None else table()


def resolve_old(module: str, name: str, mark: Deprecated) -> object:
    """The object old name ``module.name`` now spells, after warning once that it goes.

    Called on every lookup and never cached in the module, so each use
    reaches it.
    """
    from untaped.sdk._replacement import sdk_replacement_text  # noqa: PLC0415 - on use only
    from untaped.sdk.marks import notify_deprecation  # noqa: PLC0415

    notify_deprecation(
        f"sdk {module}.{name}",
        f"`{module}.{name}`",
        removed_in=mark.removed_in,
        replacement=sdk_replacement_text(mark),
    )
    return mark.replacement


__all__ = ["OLD_NAMES_ATTR", "lazy_names", "old_names_of", "resolve_old"]
