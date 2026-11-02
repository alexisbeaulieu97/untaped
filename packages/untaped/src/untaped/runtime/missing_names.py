"""A name of untaped or another plugin that a plugin's code reached, and its owner doesn't have.

untaped reads no plugin's code to predict what it uses, so a name its owner
no longer has (or doesn't have yet) shows when the code runs: while the
plugin loads (composition quarantines it ``uses-missing``) or while its
command runs (the error boundary, :func:`explain`, exit 4). :func:`missed`
reads the name from the exception itself, never from its text:

- ``AttributeError``: its ``obj`` (a module, class or instance) and ``name``;
- ``ModuleNotFoundError``: its ``name``; another ``ImportError``: its
  ``name`` and ``name_from`` (``from M import N``).

The plugin is the one owning the deepest frame in a plugin's package; the
frames after it may only be the import machinery or a module's or
pydantic's ``__getattr__``. One rule says the rest: the error is translated
only when the exception shows that a public module or class of another
distribution (untaped's, or another plugin's) lacks the name. A case it
doesn't cover keeps its traceback: a name the owner's own code misses, a
class's own ``__getattr__``, a name the module lists in ``__all__`` and
fails to give, a submodule the plugin never imported, a field read on its
model class, a namespace package or a module right under ``untaped``
(where nested plugins live), and a name of the plugin's own or of a
library. The name is spelled by the module plugins import it from
(:func:`~untaped.sdk._parameter_marks.public_name`).

The words state facts, never a cause (:func:`lacks`): the installed owner,
the plugin's requirement on it as declared, and both fixes.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import CodeType, FrameType, ModuleType
from typing import TYPE_CHECKING

from untaped.sdk.errors import ErrorCategory, UntapedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from untaped.runtime.plugin_uses import Owner
    from untaped.runtime.registry import CompositionResult


@dataclass(frozen=True)
class Missed:
    """A name the plugin's code reached that ``owner`` doesn't have."""

    owner: Owner
    #: The name as plugins spell it: ``untaped_git.api.Prefetched.cat``.
    spelled: str
    #: The module, file and line of the plugin's deepest frame.
    module: str
    file: str
    line: int


class MissingNameError(UntapedError):
    """A plugin can't run here: it reached a name its owner no longer has (or doesn't yet)."""

    category = ErrorCategory.CONFIG


def missed(
    exc: BaseException, owners: Mapping[str, Owner], is_plugin: Callable[[str], bool]
) -> Missed | None:
    """The name ``exc`` says the plugin's code missed; ``None`` when it is anything else.

    ``owners`` maps packages to their owners (:func:`~untaped.runtime.plugin_uses.owners_of`);
    ``is_plugin`` tells whether a module is a plugin's own code.
    """
    from untaped.runtime.plugin_uses import owner_of  # noqa: PLC0415 - on an error only

    frames = list(traceback.walk_tb(exc.__traceback__))
    at = next(
        (index for index in range(len(frames) - 1, -1, -1) if is_plugin(_module(frames[index][0]))),
        None,
    )
    if at is None:
        return None
    found = _name(exc, [frame for frame, _ in frames[at + 1 :]])
    if found is None:
        return None
    module, spelled = found
    owner = owner_of(module, owners)
    if owner is None:
        return None
    frame, line = frames[at]
    return Missed(owner, spelled, _module(frame), frame.f_code.co_filename, line)


def explain(exc: BaseException) -> MissingNameError | None:
    """The error the boundary raises for ``exc``; ``None`` when it stays what it is.

    Exit 4, its traceback under ``--verbose``, whatever the plugin's
    requirement on the owner says.
    """
    from untaped.runtime import plugin_frames  # noqa: PLC0415 - on an error only
    from untaped.runtime.bootstrap import attached  # noqa: PLC0415 - bootstrap runs the boundary
    from untaped.runtime.plugin_uses import owners_of  # noqa: PLC0415

    result = attached()
    if result is None:
        return None
    found = missed(
        exc,
        owners_of(result.candidates),
        lambda module: plugin_frames.plugin_at(module, result) is not None,
    )
    plugin = None if found is None else plugin_frames.plugin_at(found.module, result)
    if found is None or plugin is None or _same(found.owner.distribution, plugin.distribution):
        return None
    facts, fix = lacks(
        found,
        installed=_installed(found.owner, result),
        distribution=plugin.distribution,
        requires_dist=plugin.requires_dist,
    )
    who = f"{plugin.distribution} {plugin.version}".strip()
    return MissingNameError(
        f"{plugin.name} can't run this: {who} uses {facts}", hint=f"{fix} (--verbose shows where)"
    )


def lacks(
    found: Missed, *, installed: str, distribution: str, requires_dist: Sequence[str]
) -> tuple[str, str]:
    """``<name>, which <owner> <installed> doesn't have (<requirement>)``, and the fix.

    ``distribution`` is the plugin's, ``requires_dist`` its ``Requires-Dist``
    lines and ``installed`` the owner's installed version (``""`` when
    unknown). The requirement is the plugin's on the owner as it declares it;
    the fix names both ways out, since the facts don't say which one it is.
    """
    owner = found.owner.distribution
    have = f"{owner} {installed}".strip()
    declared = _declared(requires_dist, owner)
    requirement = (
        f"{distribution} requires {', '.join(declared)}"
        if declared
        else f"{distribution} declares no requirement on {owner}"
    )
    fix = f"upgrade {distribution} or {owner}, or remove {distribution}"
    return f"{found.spelled}, which {have} doesn't have ({requirement})", fix


def traceback_text(exc: BaseException) -> str:
    """``exc``'s traceback, as Python prints it."""
    return "".join(traceback.format_exception(exc)).rstrip()


def _name(exc: BaseException, tail: list[FrameType]) -> tuple[str, str] | None:
    """The missing name's module and spelling, when the frames after the plugin's allow it."""
    hooks = _hooks(exc)
    if not all(_machinery(frame, hooks) for frame in tail):
        return None
    if isinstance(exc, AttributeError):
        return _attribute(exc.obj, exc.name)
    if isinstance(exc, ModuleNotFoundError):
        return _module_gone(exc.name)
    if isinstance(exc, ImportError):
        name_from = getattr(exc, "name_from", None)
        module = sys.modules.get(exc.name or "")
        if module is None or not isinstance(name_from, str) or not _settled(module, name_from):
            return None
        return module.__name__, f"{module.__name__}.{name_from}"
    return None


def _attribute(obj: object, name: str | None) -> tuple[str, str] | None:
    from untaped.sdk._parameter_marks import public_name  # noqa: PLC0415 - on an error only

    if obj is None or name is None:
        return None
    if isinstance(obj, ModuleType):
        if not _settled(obj, name) or _submodule(obj, name):
            return None
        return obj.__name__, f"{obj.__name__}.{name}"
    cls = obj if isinstance(obj, type) else type(obj)
    if obj is cls and name in (getattr(cls, "__pydantic_fields__", None) or {}):
        return None  # a field read on its model class: it has it, on its instances
    return cls.__module__, f"{public_name(cls)}.{name}"


def _settled(module: ModuleType, name: str) -> bool:
    """Whether ``module`` missing ``name`` says its owner doesn't have it.

    Not when the module lists it (failing to give it is the owner's bug), nor
    when it is a namespace package or ``untaped`` itself: their names are
    whatever is installed into them (a plugin nested in ``untaped.plugins``),
    whoever owns the package around them.
    """
    if module.__name__ == "untaped" or getattr(module, "__file__", None) is None:
        return False
    return not _listed(module, name)


def _listed(module: ModuleType, name: str) -> bool:
    """Whether ``module`` says it has ``name``: then failing to give it is the owner's bug."""
    listed = getattr(module, "__all__", ())
    return isinstance(listed, (list, tuple)) and name in listed


def _submodule(module: ModuleType, name: str) -> bool:
    """Whether ``name`` is a submodule of ``module`` the plugin didn't import: its own slip."""
    if not hasattr(module, "__path__"):
        return False
    try:
        return importlib.util.find_spec(f"{module.__name__}.{name}") is not None
    except Exception:  # a finder failing: say nothing rather than guess
        return True


def _module_gone(name: str | None) -> tuple[str, str] | None:
    """A missing module whose parent is a regular package already loaded (not a namespace).

    A module right under ``untaped`` is where a plugin nested in untaped's
    package lives (``untaped.plugins``); core's own modules plugins use are
    deeper (``untaped.sdk.…``), so one missing there is a plugin not
    installed, not a name core removed.
    """
    parent_name = "" if name is None else name.rpartition(".")[0]
    parent = sys.modules.get(parent_name)
    if name is None or parent is None or getattr(parent, "__file__", None) is None:
        return None
    if parent_name == "untaped":
        return None
    return name, name


def _machinery(frame: FrameType, hooks: set[CodeType]) -> bool:
    """Whether ``frame`` only looks a name up: the import system, or one of ``hooks``."""
    module = _module(frame)
    return module == "importlib" or module.startswith("importlib.") or frame.f_code in hooks


def _hooks(exc: BaseException) -> set[CodeType]:
    """The lookup hooks a miss of ``exc`` may pass through.

    A module's ``__getattr__`` (its names, a lazy table's included) and
    pydantic's, on a model and on its class (whose fields are only on its
    instances, so :func:`_attribute` keeps a field read on the class). A
    class's own ``__getattr__`` decides what exists as it likes
    (``key(Model).field``), so a miss there says nothing about what its owner
    removed.
    """
    from pydantic import BaseModel  # noqa: PLC0415

    if isinstance(exc, AttributeError):
        owner = exc.obj
    elif isinstance(exc, ImportError):
        owner = sys.modules.get(exc.name or "")
    else:
        return set()
    if isinstance(owner, ModuleType):
        hook = owner.__dict__.get("__getattr__")
        code = getattr(hook, "__code__", None)
        return {code} if isinstance(code, CodeType) else set()
    if isinstance(owner, BaseModel):
        hook = vars(BaseModel).get("__getattr__")
    elif isinstance(owner, type) and issubclass(owner, BaseModel):
        hook = vars(type(BaseModel)).get("__getattr__")  # the model class's: its metaclass's
    else:
        return set()
    code = getattr(hook, "__code__", None)
    return {code} if isinstance(code, CodeType) else set()


def _module(frame: FrameType) -> str:
    return str(frame.f_globals.get("__name__", ""))


def _declared(requires_dist: Sequence[str], distribution: str) -> list[str]:
    """The plugin's requirements on ``distribution`` that apply here, as declared: ``acme>=1.2``."""
    from packaging.utils import canonicalize_name  # noqa: PLC0415

    from untaped.runtime.requirements import applies, is_plain, parse_requirement  # noqa: PLC0415

    wanted = canonicalize_name(distribution)
    found: list[str] = []
    for line in requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or not is_plain(parsed) or canonicalize_name(parsed.name) != wanted:
            continue
        try:
            if not applies(parsed):
                continue
        except Exception:  # a marker this Python can't evaluate: it doesn't apply
            continue
        found.append(f"{parsed.name}{parsed.specifier}")
    return found


def _same(one: str, other: str) -> bool:
    from packaging.utils import canonicalize_name  # noqa: PLC0415

    return canonicalize_name(one) == canonicalize_name(other)


def _installed(owner: Owner, result: CompositionResult) -> str:
    """``owner``'s installed version; ``""`` when unknown."""
    if owner.core:
        from importlib import metadata  # noqa: PLC0415

        try:
            return metadata.version(owner.distribution)
        except metadata.PackageNotFoundError:
            return ""
    return next(
        (
            each.distribution_version
            for each in result.candidates
            if _same(each.distribution, owner.distribution)
        ),
        "",
    )


__all__ = ["Missed", "MissingNameError", "explain", "lacks", "missed", "traceback_text"]
