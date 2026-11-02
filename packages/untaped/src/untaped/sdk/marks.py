"""Stability marks: which commands, fields and APIs are experimental or deprecated.

The author marks the class once (``stability = experimental`` or
``stability = deprecated(replacement=...)`` on a command, group or plugin
class); core reads the mark for help, warnings and checks. This module holds
the markers and why a value is none (:func:`stability_refusal`), the removal
version every deprecation names (``removed_in``, rendered by
:func:`removal_text`), and the one emitter of a deprecation on the sink,
:func:`notify_deprecation`. A deprecated parameter's mark is in
:mod:`untaped.sdk._parameter_marks`, a deprecated behaviour in
:mod:`untaped.sdk._behaviours`, and the help panels and the tree-wide query
in :mod:`untaped.runtime.stability_help` and :mod:`untaped.runtime.stability_marks`.

Neither marker is, or subclasses, ``warnings.deprecated``: that one warns on
every use and, on a settings field, becomes pydantic field deprecation.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cyclopts import App

from untaped.sdk._keys import SettingKey
from untaped.sdk._lazy import STABILITY_ATTR
from untaped.sdk.words import (
    deprecated_ignored_message,
    deprecated_message,
)

_ROOT_NAME = "untaped"
_FUNCTION_ATTR = STABILITY_ATTR


class _Marker:
    """What both markers share: used as a decorator, they record themselves on what they mark."""

    def __call__[F: Callable[..., Any]](self, target: F, /) -> F:
        """Record this mark on ``target`` and return it unchanged.

        A plugin decorates its Python API this way (a function, method or
        class), and marks a command, group or plugin class with ``stability =``
        instead; core marks the function it builds for a command too. On a
        descriptor (``classmethod``, ``staticmethod``, ``property``) the mark
        lands on its function, where the surface walker reads it.
        """
        setattr(descriptor_function(target), _FUNCTION_ATTR, self)
        return target


def descriptor_function(obj: object) -> object:
    """The function under a ``classmethod``, ``staticmethod``, ``property`` or bound method."""
    if isinstance(obj, property):
        return obj.fget if obj.fget is not None else obj
    if isinstance(obj, classmethod | staticmethod | types.MethodType):
        return obj.__func__
    return obj


@dataclass(frozen=True)
class Experimental(_Marker):
    """The ``experimental`` marker: may change or go away in a minor release."""


def check_removed_in(value: object) -> int | None:
    """``value`` when it is ``None`` or a major number (an ``int`` of at least 1, not a bool).

    Raises ``TypeError`` otherwise: a string or a minor would invite
    ``"12.1"``, which no release check should have to interpret.
    """
    if value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        return value
    raise TypeError(f"removed_in is the major number that removes it: removed_in=13, not {value!r}")


@dataclass(frozen=True, kw_only=True)
class Deprecated(_Marker):
    """The ``deprecated`` marker: still works, with a warning, until its removal version.

    ``removed_in`` is the major that removes it (``13``); ``None`` is undated
    and reads "a future major release". ``replacement`` is a command class
    or a ``key(Model).field`` (untaped spells either), the function or class
    that replaces a Python API, or text: ``"untaped github sync"`` or a
    setting key (shown in backticks) or prose (shown as is). Core's own
    adapter also passes the app it builds for a command.

    First consumer: the 12 to 13 transition.
    """

    replacement: Callable[..., Any] | App | SettingKey | str | None = None
    removed_in: int | None = None

    def __call__[F: Callable[..., Any]](self, target: F, /) -> F:
        """Record this mark on ``target``, and its ``warnings.deprecated`` literal when it has one.

        The literal is how the warning that decorator raises finds its
        callable again (:class:`UntapedDeprecationWarning` built from the
        literal alone), so the mark goes over it.
        """
        marked = descriptor_function(target)
        if isinstance(marked, type):
            literal = vars(marked).get("__deprecated__")
        else:
            literal = getattr(marked, "__deprecated__", None)
        if isinstance(literal, str):
            named = _LITERALS.setdefault(literal, [])
            # Marked again (a reloaded module): the newer object replaces the older.
            named[:] = [each for each in named if _spelling(each) != _spelling(marked)]
            named.append(marked)
            _FIELDS.pop(literal, None)
        return super().__call__(target)

    def __post_init__(self) -> None:
        check_removed_in(self.removed_in)
        replacement = self.replacement
        if replacement is None or callable(replacement) or isinstance(replacement, SettingKey):
            return
        if not isinstance(replacement, str) or not replacement.strip():
            raise TypeError(
                "deprecated(replacement=...) takes a command class, a function, a key(...) "
                f"or non-empty text, not {replacement!r}"
            )


experimental = Experimental()
"""Mark a command, group or plugin experimental (``@experimental``, ``stability=``)."""


def deprecated(
    *,
    removed_in: int | None = None,
    replacement: Callable[..., Any] | App | str | None = None,
) -> Deprecated:
    """Mark a command, group, plugin or setting deprecated, naming its removal and replacement.

    ``removed_in`` is the major that removes it (``removed_in=13``; a plugin's
    own major for a plugin's command); without it the removal reads "a future
    major release", which ``plugin check`` reports. ``replacement`` is one of
    the plugin's own command or group classes (``replacement=Set``), which
    help and the warning spell as `` `untaped svc set` ``, or text: another
    plugin's command (``"untaped other run"``), a setting key or prose. Both
    are keyword-only.
    Use it as ``stability = deprecated(...)``, or in a field's ``Annotated``.

    First consumer: the 12 to 13 transition.
    """
    return Deprecated(replacement=replacement, removed_in=removed_in)


type Stability = Experimental | Deprecated


def old_names(**entries: Deprecated) -> Mapping[str, Deprecated]:
    """A module's old spellings: each resolves to its replacement and warns until it goes.

    ``old_names(human_size=deprecated(removed_in=14, replacement=size_text))``
    in the table a module resolves its names from (the SDK root's
    ``_exports.OLD_NAMES``, a group's ``lazy_names(..., old=...)``). Each
    entry names its removal and the renamed class or function itself, never
    text: the old name is that object. Old names stay out of ``__all__``;
    the module's ``TYPE_CHECKING`` block assigns each (``human_size =
    size_text``) so type checkers accept it.
    """
    for name, mark in entries.items():
        if (
            not isinstance(mark, Deprecated)
            or mark.removed_in is None
            or mark.replacement is None
            or isinstance(mark.replacement, str)
        ):
            raise TypeError(
                f"old_names({name}=...) takes deprecated(removed_in=<major>, replacement=<the "
                f"renamed object>), not {mark!r}"
            )
    return types.MappingProxyType(dict(entries))


def stability_refusal(value: object) -> str | None:
    """Why ``value`` is no stability mark; ``None`` for no mark or a mark.

    The marks are ``experimental`` and ``deprecated(...)``. The hint names
    the two likely mistakes: ``deprecated`` without its call and Python's
    own ``warnings.deprecated("...")``.
    """
    if value is None or isinstance(value, Experimental | Deprecated):
        return None
    hint = (
        "call it: deprecated(removed_in=..., replacement=...)"
        if value is deprecated
        else "use untaped.sdk.experimental or untaped.sdk.deprecated(removed_in=..., "
        "replacement=...), not warnings.deprecated"
    )
    return f"holds {value!r}, not experimental or deprecated(...); {hint}"


def function_mark(func: object) -> Stability | None:
    """The mark a decorator recorded on ``func``: a plugin's API, or a function core built."""
    mark = getattr(func, _FUNCTION_ATTR, None)
    return mark if isinstance(mark, Experimental | Deprecated) else None


def class_mark(cls: type) -> Stability | None:
    """The marker put on ``cls`` itself; one a base class carries is not inherited."""
    mark = vars(cls).get(_FUNCTION_ATTR)
    return mark if isinstance(mark, Experimental | Deprecated) else None


# --- marks on settings -------------------------------------------------------


type SettingStability = Literal["stable", "experimental", "deprecated"]


def stability_name(mark: Stability | None) -> SettingStability:
    """``mark`` as the word ``config list`` and ``config get`` show."""
    if mark is None:
        return "stable"
    return "experimental" if isinstance(mark, Experimental) else "deprecated"


# --- removal versions and the deprecation emitter ------------------------------

# Plugin name → its distribution, bound by the composition (``bind_owners``):
# a plugin's deprecations name whose major they mean, core's own name none.
_OWNERS: dict[str, str] = {}


def bind_owners(owners: Mapping[str, str]) -> None:
    """Record each composed plugin's distribution (plugin name → distribution), anew."""
    _OWNERS.clear()
    _OWNERS.update(owners)
    _FIELDS.clear()


def owner(name: str) -> str | None:
    """The distribution of composed plugin ``name`` (a command's first word, a setting's section).

    ``None`` for core's own commands and sections, and outside a composition.
    """
    return _OWNERS.get(name)


def path_owner(path: tuple[str, ...]) -> str | None:
    """The owner of the command at ``path`` (``("untaped", "awx", "put")``)."""
    return owner(path[1]) if len(path) > 1 else None


def removal_text(removed_in: int | None, owner: str | None) -> str | None:
    """``12.0`` for core's own, ``untaped-awx 12.0`` for a plugin's, ``None`` when undated.

    A plugin's major is its own, so its text names its distribution, whoever
    maintains it.
    """
    if removed_in is None:
        return None
    return f"{removed_in}.0" if owner is None else f"{owner} {removed_in}.0"


def _spelling(obj: object) -> tuple[str, str]:
    return str(getattr(obj, "__module__", "")), str(getattr(obj, "__qualname__", ""))


# A ``warnings.deprecated`` literal → the callables it decorates, recorded by the
# ``deprecated`` mark put over each.
_LITERALS: dict[str, list[object]] = {}


class UntapedDeprecationWarning(DeprecationWarning):
    """A deprecation of a Python API, carrying what the ``untaped.deprecation`` record carries.

    ``id`` is the deprecated thing's surface identity, ``removed_in`` its
    removal major (``None`` undated), ``distribution`` its owner (``None``
    for core's own) and ``replacement`` what to use instead.

    Built from its message alone, as
    ``@warnings.deprecated("<literal>", category=UntapedDeprecationWarning)``
    builds it, it reads them from the callable whose literal the message
    is, when untaped's ``deprecated`` mark sits over that decorator and no
    other marked callable has the same literal; ``id`` stays ``None``
    otherwise, and the warning shows as a plain warning line.

    First consumer: the 12 to 13 transition.
    """

    def __init__(
        self,
        message: str,
        *,
        id: str | None = None,
        removed_in: int | None = None,
        distribution: str | None = None,
        replacement: str | None = None,
    ) -> None:
        super().__init__(message)
        found = _literal_fields(message) if id is None else None
        if found is not None:
            id, removed_in, distribution, replacement = found
        self.id = id
        self.removed_in = removed_in
        self.distribution = distribution
        self.replacement = replacement


# A literal → its callable's record fields, read once per composition (``bind_owners``):
# Python builds the warning on every call, shown or not.
_FIELDS: dict[str, tuple[str, int | None, str | None, str | None]] = {}


def _literal_fields(message: str) -> tuple[str, int | None, str | None, str | None] | None:
    """The record fields of the callable whose ``warnings.deprecated`` literal ``message`` is."""
    if message in _FIELDS:
        return _FIELDS[message]
    targets = _LITERALS.get(message, [])
    if len(targets) != 1:
        return None  # two callables spelled alike: a record could name the wrong one
    (target,) = targets
    mark = class_mark(target) if isinstance(target, type) else function_mark(target)
    if not isinstance(mark, Deprecated):
        return None
    from untaped.sdk._parameter_marks import callable_fields  # noqa: PLC0415 - a cycle

    id, distribution, replacement = callable_fields(target, mark)
    _FIELDS[message] = found = (id, mark.removed_in, distribution, replacement)
    return found


_NOTIFIED: set[str] = set()


def first_notice(key: str) -> bool:
    """Whether ``key`` has not been warned about in this process yet (and remember it)."""
    if key in _NOTIFIED:
        return False
    _NOTIFIED.add(key)
    return True


def forget_notices() -> None:
    """Forget which deprecations were warned about (tests, embedding)."""
    _NOTIFIED.clear()


def notify_deprecation(
    id: str,
    subject: str,
    *,
    removed_in: int | None,
    owner: str | None = None,
    replacement: str | None = None,
    kept: str | None = None,
    key: str | None = None,
    hint: str | None = None,
    plugin: str | None = None,
) -> None:
    """Warn once that ``subject`` is deprecated, as an ``untaped.deprecation`` record.

    The one emitter of a deprecation on the sink: ``id`` is the deprecated
    thing's surface identity (``cli untaped awx put``), ``subject`` how the
    sentence names it, ``replacement`` the replacement as shown (backticks
    included). ``kept`` says the old spelling was ignored because ``kept``
    is also set. Once per process per ``key`` (else per ``id``), so callers
    warning about one thing in different words share it. ``hint`` is the
    follow-up, its own line and its own field under JSON. ``plugin`` names
    the plugin that uses the deprecated thing, when one does.
    """
    if not first_notice(id if key is None else key):
        return
    from untaped.runtime.run_output import Diagnostics  # noqa: PLC0415
    from untaped.sdk.diagnostic import Deprecation  # noqa: PLC0415 - off the CLI path

    removal = removal_text(removed_in, owner)
    if kept is None:
        message = deprecated_message(subject, replacement, removal)
    else:
        message = deprecated_ignored_message(subject, kept, replacement or kept, removal)
    Diagnostics().emit(
        Deprecation(
            message=message,
            hint=hint,
            id=id,
            removed_in=removed_in,
            distribution=owner,
            replacement=None if replacement is None else replacement.replace("`", ""),
            plugin=plugin,
        )
    )
