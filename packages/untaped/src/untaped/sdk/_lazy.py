"""A group's names, each loaded from its home module on first use.

A group's ``__init__`` lists its names but imports none of them: the
modules inside a group import one another, and the runtime imports some of
them, so a group that loaded every member on import would load the run with
the first of its modules anyone touched. :func:`lazy_names` gives the group
its module ``__getattr__`` and ``__dir__`` (PEP 562), and refuses a table
that disagrees with the group's ``__all__``, so the two lists cannot drift.

A deprecated name is seen when it is looked up, not only when it is called:
a name whose object carries a ``deprecated`` mark, and an old name of the
group's ``old=`` table (:func:`untaped.sdk.marks.old_names`), warn at the
line that looks them up and are never kept in the group's globals, so every
lookup warns. The warning is an
:class:`~untaped.sdk.marks.UntapedDeprecationWarning`, which a run's
warning route shows and records like any other. :func:`peek` reads a name
without warning, for untaped's own readers (the surface, lint).
"""

from __future__ import annotations

import functools
import importlib
import os
import types
import warnings
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.sdk.marks import Deprecated


#: Where a module keeps its old-names table (a function returning it), for the surface walker.
OLD_NAMES_ATTR = "__untaped_old_names__"
#: Where a lazy module keeps its resolver: a name's object, read without warning or keeping it.
RESOLVE_ATTR = "__untaped_resolve__"
#: Where the ``deprecated`` and ``experimental`` marks record themselves (``untaped.sdk.marks``).
STABILITY_ATTR = "__untaped_stability__"

#: The files whose frames a lookup's warning skips: this one, and the SDK root's resolver.
_LOOKUP_FILES = (__file__, os.path.join(os.path.dirname(__file__), "__init__.py"))


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

    def resolve(name: str) -> object:
        if name in home:
            return getattr(importlib.import_module(home[name]), name)
        mark = table().get(name) if not name.startswith("__") else None
        if mark is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        return mark.replacement

    namespace[RESOLVE_ATTR] = resolve

    def __getattr__(name: str) -> object:
        old = None if name in home else table().get(name)
        return looked_up(namespace, package, name, resolve(name), old)

    def __dir__() -> list[str]:
        return sorted({*namespace, *home})

    return __getattr__, __dir__


def looked_up(
    namespace: MutableMapping[str, object],
    module: str,
    name: str,
    value: object,
    old: Deprecated | None,
) -> object:
    """``value``, which ``module.name`` resolved to: kept in ``namespace`` unless deprecated.

    An old name (its mark is ``old``) or a name whose object is marked
    deprecated warns at the lookup's line instead, on every lookup.
    """
    if old is not None:
        _warn_old(module, name, old)
        return value
    if _carries_mark(value):
        from untaped.sdk.marks import Deprecated, class_mark, function_mark  # noqa: PLC0415

        mark = class_mark(value) if isinstance(value, type) else function_mark(value)
        if isinstance(mark, Deprecated):
            _warn_marked(value, mark)
            return value
    namespace[name] = value
    return value


def peek(module: types.ModuleType, name: str) -> object:
    """``module.name`` as a lookup gives it, without warning or keeping it.

    A lazy module's resolver, else the attribute; ``AttributeError`` when
    the module has no such name.
    """
    if name in vars(module):
        return vars(module)[name]
    resolve = vars(module).get(RESOLVE_ATTR)
    return resolve(name) if callable(resolve) else getattr(module, name)


def old_names_of(module: types.ModuleType) -> Mapping[str, Deprecated]:
    """``module``'s old names, from the table it resolves them from; none when it has none."""
    table = vars(module).get(OLD_NAMES_ATTR)
    return {} if table is None else table()


def _carries_mark(value: object) -> bool:
    """Whether ``value`` (a function or class) carries a stability mark of its own.

    Read without importing the marks; an instance of a marked class has none of its own.
    """
    try:
        return STABILITY_ATTR in vars(value)
    except TypeError:  # no __dict__: nothing marked it
        return False


def _warn_old(module: str, name: str, mark: Deprecated) -> None:
    from untaped.sdk._parameter_marks import module_owner  # noqa: PLC0415 - on a use only
    from untaped.sdk._replacement import sdk_replacement_text  # noqa: PLC0415
    from untaped.sdk.marks import removal_text  # noqa: PLC0415
    from untaped.sdk.words import deprecated_message  # noqa: PLC0415

    owner = module_owner(module)
    replacement = sdk_replacement_text(mark)
    _warn(
        deprecated_message(f"`{module}.{name}`", replacement, removal_text(mark.removed_in, owner)),
        id=f"sdk {module}.{name}",
        removed_in=mark.removed_in,
        distribution=owner,
        replacement=None if replacement is None else replacement.replace("`", ""),
    )


def _warn_marked(value: object, mark: Deprecated) -> None:
    """Warn as calling ``value`` would: the same id, so a run shows one line for both."""
    from untaped.sdk._parameter_marks import (  # noqa: PLC0415 - on a use only
        callable_fields,
        deprecation_sentence,
    )

    id, distribution, replacement = callable_fields(value, mark)
    _warn(
        deprecation_sentence(value, mark),
        id=id,
        removed_in=mark.removed_in,
        distribution=distribution,
        replacement=replacement,
    )


def _warn(
    message: str,
    *,
    id: str,
    removed_in: int | None,
    distribution: str | None,
    replacement: str | None,
) -> None:
    """Warn at the line that looked the name up: this module's frames and importlib's skipped."""
    from untaped.sdk.marks import UntapedDeprecationWarning  # noqa: PLC0415 - on a use only

    warnings.warn(
        UntapedDeprecationWarning(
            message,
            id=id,
            removed_in=removed_in,
            distribution=distribution,
            replacement=replacement,
        ),
        stacklevel=2,
        skip_file_prefixes=_LOOKUP_FILES,
    )


__all__ = [
    "OLD_NAMES_ATTR",
    "RESOLVE_ATTR",
    "STABILITY_ATTR",
    "lazy_names",
    "looked_up",
    "old_names_of",
    "peek",
]
