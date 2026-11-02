"""Whether a name a plugin's code may import carries a deprecation mark: :func:`deprecation_of`."""

from __future__ import annotations

import importlib
import importlib.util
import types

from untaped.sdk._lazy import old_names_of, peek
from untaped.sdk.marks import (
    Deprecated,
    class_mark,
    descriptor_function,
    experimental,
    function_mark,
)


@experimental
def deprecation_of(module: str | types.ModuleType, name: str) -> str | None:
    """The sentence a use of ``module.name`` warns with; ``None`` without a deprecation mark.

    The sentence is built from the mark, as the warning's is
    (``untaped.sdk.words``). The name is found as ``from module import name``
    finds it: an old name of the module's table, an attribute (a lazy
    module's included, read without the warning a lookup gives) or a
    submodule. ``AttributeError`` when the module has no such name; anything
    the module raises importing or giving it propagates. A linter reads an
    author's imports with it (``untaped lint``'s ``uses-deprecated``).
    """
    from untaped.sdk._parameter_marks import module_owner  # noqa: PLC0415 - on a check only
    from untaped.sdk._replacement import sdk_replacement_text  # noqa: PLC0415
    from untaped.sdk.marks import removal_text  # noqa: PLC0415
    from untaped.sdk.words import deprecated_message  # noqa: PLC0415

    found = importlib.import_module(module) if isinstance(module, str) else module
    mark = _mark(found, name)
    if mark is None:
        return None
    removal = removal_text(mark.removed_in, module_owner(found.__name__))
    return deprecated_message(f"`{found.__name__}.{name}`", sdk_replacement_text(mark), removal)


def _mark(module: types.ModuleType, name: str) -> Deprecated | None:
    old = old_names_of(module)
    if name in old:
        return old[name]
    try:
        obj = peek(module, name)
    except AttributeError:
        if hasattr(module, "__path__") and importlib.util.find_spec(f"{module.__name__}.{name}"):
            return None  # a submodule: modules carry no mark
        raise
    target = descriptor_function(obj)
    mark = class_mark(target) if isinstance(target, type) else function_mark(target)
    return mark if isinstance(mark, Deprecated) else None
