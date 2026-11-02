"""A name of untaped or another plugin that a plugin's code reached, and its owner doesn't have.

The startup check (:mod:`untaped.runtime.plugin_uses`) finds the names a
plugin imports; a name reached on an object a call returned, or looked up
while a module loads, shows only when the code runs. :func:`missed` reads
it from the exception itself, never from its text:

- ``AttributeError``: its ``obj`` (a module, class or instance) and ``name``;
- ``ModuleNotFoundError``: its ``name``; another ``ImportError``: its
  ``name`` and ``name_from`` (``from M import N``);
- pydantic's ``ValidationError`` whose every error is ``extra_forbidden``,
  raised by ``BaseModel.__init__`` called straight from the plugin's code (a
  field it passes that the model no longer has). The same error from
  ``model_validate`` on data a user wrote is their mistyped key, never this.

The plugin is the one owning the deepest frame in a plugin's package; the
frames after it may only be the import machinery, a module's or pydantic's
``__getattr__`` (or that ``__init__``), so a name the owner's own code
misses stays the owner's bug, as does a name the module lists in
``__all__`` and fails to give. A submodule the plugin never imported, a
nested model's field and a module right under ``untaped`` (where nested
plugins live) are the plugin's slip or a plugin not installed, not a name
removed. The owner is untaped or another plugin's distribution, found
by the name's module; a library's name, or the plugin's own, is a bug and
stays one. The name is spelled by the module plugins import it from
(:func:`~untaped.sdk._parameter_marks.public_name`).

The error boundary (:func:`untaped.runtime.cli_run.report_errors`) and
composition's import of a plugin's class read it.
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
    from pydantic import ValidationError

    from untaped.runtime.plugin_uses import Owner
    from untaped.runtime.registry import CompositionResult


@dataclass(frozen=True)
class Missed:
    """A name the plugin's code reached that ``owner`` doesn't have."""

    owner: Owner
    #: The name as plugins spell it: ``untaped_git.api.Prefetched.cat``, ``untaped.sdk.Row(x=)``.
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


def explain(exc: BaseException) -> MissingNameError | str | None:
    """What the error boundary says for ``exc``: an error, a line before its traceback, or nothing.

    A name that can have gone (a major lies between the plugin's floor on its
    owner and the installed owner, or there is no floor) is the error, exit
    4, its traceback under ``--verbose``. A name that can't have gone (a
    typo, or a floor set too low) keeps the traceback, with the returned
    line before it.
    """
    from untaped.runtime import plugin_frames  # noqa: PLC0415 - on an error only
    from untaped.runtime.bootstrap import attached  # noqa: PLC0415 - bootstrap runs the boundary
    from untaped.runtime.plugin_uses import floor_rule, owners_of  # noqa: PLC0415

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
    installed = _installed(found.owner, result)
    rule = floor_rule(
        distribution=plugin.distribution,
        requires_dist=plugin.requires_dist,
        owner=found.owner.distribution,
        installed=installed,
    )
    have = f"{found.owner.distribution} {installed}".strip()
    facts = f"it uses {found.spelled}, which {have} doesn't have{rule.clause}"
    if not rule.removed:
        return f"{plugin.name}: {facts}"
    return MissingNameError(
        f"{plugin.name} can't run this: {facts}", hint=f"{rule.fix} (--verbose shows where)"
    )


def traceback_text(exc: BaseException) -> str:
    """``exc``'s traceback, as Python prints it."""
    return "".join(traceback.format_exception(exc)).rstrip()


def _name(exc: BaseException, tail: list[FrameType]) -> tuple[str, str] | None:
    """The missing name's module and spelling, when the frames after the plugin's allow it."""
    from pydantic import ValidationError  # noqa: PLC0415 - loaded already by any command

    if isinstance(exc, ValidationError):
        return _field(exc, tail)
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
        if module is None or not isinstance(name_from, str) or _listed(module, name_from):
            return None
        return module.__name__, f"{module.__name__}.{name_from}"
    return None


def _attribute(obj: object, name: str | None) -> tuple[str, str] | None:
    from untaped.sdk._parameter_marks import public_name  # noqa: PLC0415 - on an error only

    if obj is None or name is None:
        return None
    if isinstance(obj, ModuleType):
        if _listed(obj, name) or _submodule(obj, name):
            return None
        return obj.__name__, f"{obj.__name__}.{name}"
    cls = obj if isinstance(obj, type) else type(obj)
    return cls.__module__, f"{public_name(cls)}.{name}"


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


def _field(exc: ValidationError, tail: list[FrameType]) -> tuple[str, str] | None:
    """A model's own fields the plugin passed its ``__init__`` that the model doesn't have."""
    from pydantic import BaseModel  # noqa: PLC0415

    from untaped.sdk._parameter_marks import public_name  # noqa: PLC0415 - on an error only

    errors = exc.errors()
    # A keyword the plugin wrote: a field of the model itself, not one nested in a value.
    if not errors or any(
        each["type"] != "extra_forbidden" or len(each["loc"]) != 1 for each in errors
    ):
        return None
    if len(tail) != 1 or tail[0].f_code is not BaseModel.__init__.__code__:
        return None
    model = type(tail[0].f_locals.get("self"))
    fields = sorted({str(each["loc"][0]) for each in errors})
    spelled = ", ".join(f"{public_name(model)}({field}=)" for field in fields)
    return model.__module__, spelled


def _machinery(frame: FrameType, hooks: set[CodeType]) -> bool:
    """Whether ``frame`` only looks a name up: the import system, or one of ``hooks``."""
    module = _module(frame)
    return module == "importlib" or module.startswith("importlib.") or frame.f_code in hooks


def _hooks(exc: BaseException) -> set[CodeType]:
    """The lookup hooks a miss of ``exc`` may pass through.

    A module's ``__getattr__`` (its names, a lazy table's included) and
    pydantic's, which only adds a model's private and extra attributes. A
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
    if isinstance(owner, BaseModel) or (isinstance(owner, type) and issubclass(owner, BaseModel)):
        code = getattr(vars(BaseModel).get("__getattr__"), "__code__", None)
        return {code} if isinstance(code, CodeType) else set()
    return set()


def _module(frame: FrameType) -> str:
    return str(frame.f_globals.get("__name__", ""))


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


__all__ = ["Missed", "MissingNameError", "explain", "missed", "traceback_text"]
