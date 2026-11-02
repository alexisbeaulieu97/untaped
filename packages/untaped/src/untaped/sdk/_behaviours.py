"""Deprecated behaviours: a change in what a command does.

A behaviour has no object to mark, so the plugin class attribute holding one names it.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence

from untaped.sdk._block import OwnedBlock
from untaped.sdk._replacement import replacement_text
from untaped.sdk.marks import (
    _ROOT_NAME,
    Deprecated,
    UntapedDeprecationWarning,
    notify_deprecation,
    owner,
    removal_text,
)
from untaped.sdk.words import (
    deprecated_message,
)

_BEHAVIOUR_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


_CORE_OWNER = _ROOT_NAME


class _Default:
    """The behaviour's own replacement, where ``replacement=None`` would mean none."""

    def __repr__(self) -> str:
        return "<the behaviour's replacement>"


_OWN = _Default()


class DeprecatedBehaviour(OwnedBlock):
    """A deprecated behaviour: a promise with no object to mark, so it carries a name.

    A plugin class holding it names it: ``bare_ids =
    DeprecatedBehaviour(removed_in=13, replacement="--id NAME")`` is the
    plugin's behaviour ``bare-ids``. Core's own behaviours, which are never
    ``Core`` attributes, are named where they are declared
    (``DeprecatedBehaviour("old-login", deprecated(removed_in=13))``) and listed
    by ``_core_behaviours``. The surface writes
    ``behaviour <plugin>.<name>`` for a listed one, so the release check holds
    it to its date; a held one reaches the surface once it is read from the
    declaration, with the mount. A site never types the words: it names the subject and calls
    :meth:`sentence`, :meth:`notify` or :meth:`warn`.

    First consumer: the 12 to 13 transition.
    """

    block_kind = "deprecated-behaviour"
    name_style = "kebab"

    mark: Deprecated

    def __init__(
        self,
        name: str | None = None,
        mark: Deprecated | None = None,
        *,
        removed_in: int | None = None,
        replacement: str | None = None,
    ) -> None:
        super().__init__()
        if (name is None) != (mark is None):
            raise TypeError(
                "a deprecated behaviour takes its name and mark together, or removed_in= and "
                "replacement= when a plugin class holds it"
            )
        if name is not None and (problem := self.name_problem(name)) is not None:
            raise TypeError(problem)
        if mark is None:
            mark = Deprecated(replacement=replacement, removed_in=removed_in)
        elif removed_in is not None or replacement is not None:
            raise TypeError("a deprecated behaviour's mark already says removed_in and replacement")
        if not isinstance(mark, Deprecated) or callable(mark.replacement):
            raise TypeError("a deprecated behaviour's replacement is text or None")
        if mark.removed_in is None:
            raise TypeError("a deprecated behaviour names the major that removes it: removed_in=13")
        self._spelled_name = name
        self.mark = mark

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if isinstance(name, str) and _BEHAVIOUR_NAME.fullmatch(name):
            return None
        return f"a deprecated behaviour's name is kebab-case (row-kind), not {name!r}"

    @property
    def spelled(self) -> bool:
        return self._spelled_name is not None

    @property
    def name(self) -> str:
        return self._spelled_name if self._spelled_name is not None else super().name

    @property
    def plugin(self) -> str:
        """The plugin it belongs to: the one holding it, or listing it (``untaped`` for core's)."""
        if self._spelled_name is None:
            return super().plugin
        return behaviour_owner(self) or _CORE_OWNER

    @property
    def where(self) -> str:
        return self._spelled_name if self._spelled_name is not None else super().where

    def __repr__(self) -> str:
        return f"DeprecatedBehaviour({self.where!r}, removed_in={self.mark.removed_in})"

    @property
    def replacement(self) -> str | None:
        """The replacement as warnings show it (a command or key backticked), or ``None``."""
        return replacement_text(self.mark, None)

    @property
    def id(self) -> str:
        """Its surface identity: ``behaviour <plugin>.<name>``, with ``untaped`` for core's.

        The plugin is the one holding or listing it (:attr:`plugin`).
        """
        return f"behaviour {self.plugin}.{self.name}"

    def _shown(self, replacement: str | None | _Default) -> str | None:
        if isinstance(replacement, _Default):
            return self.replacement
        if replacement is None:
            return None
        return replacement_text(Deprecated(replacement=replacement), None)

    def sentence(self, subject: str, *, replacement: str | None | _Default = _OWN) -> str:
        """``<subject> is deprecated and will be removed in <version>[; use <replacement>]``.

        ``subject`` names the support that goes away ("support for a pipe
        envelope on stdin"). ``replacement`` overrides the behaviour's own
        for one site (text, or ``None`` for none: a row that shows its fix already).
        """
        removal = removal_text(self.mark.removed_in, _distribution(self))
        return deprecated_message(subject, self._shown(replacement), removal)

    def notify(
        self,
        subject: str,
        *,
        key: str,
        hint: str | None = None,
        replacement: str | None | _Default = _OWN,
    ) -> None:
        """Warn once per ``key`` on the sink, as an ``untaped.deprecation`` record.

        ``key`` is this behaviour's own (two sites passing one key share one
        warning); ``hint`` is the follow-up (:func:`~untaped.sdk.words.run_hint`),
        its own line and its own field under JSON.
        """
        notify_deprecation(
            self.id,
            subject,
            removed_in=self.mark.removed_in,
            owner=_distribution(self),
            replacement=self._shown(replacement),
            key=f"{self.id} {key}",
            hint=hint,
        )

    def warn(
        self, subject: str, *, stacklevel: int = 2, replacement: str | None | _Default = _OWN
    ) -> None:
        """Warn with an :class:`UntapedDeprecationWarning`, for a behaviour of a Python API.

        ``stacklevel`` counts from the caller of ``warn``, as ``warnings.warn``'s does.
        """
        shown = self._shown(replacement)
        warning = UntapedDeprecationWarning(
            self.sentence(subject, replacement=replacement),
            id=self.id,
            removed_in=self.mark.removed_in,
            distribution=_distribution(self),
            replacement=None if shown is None else shown.replace("`", ""),
        )
        warnings.warn(warning, stacklevel=stacklevel + 1)  # untaped: allow warnings-warn


def _core_behaviours() -> Sequence[DeprecatedBehaviour]:
    """Core's own deprecated behaviours, the SDK's (none today).

    Never ``Core`` attributes (core move ruling 3): one is listed here,
    imported from beside the mechanic that reads it, until its major removes it.
    """
    return ()


# Plugin name → a function reading its ``deprecations``, bound by the
# composition and called only when a behaviour first asks who lists it, so
# composing never imports a plugin class. Core's are the rest.
_LISTINGS: dict[str, Callable[[], Sequence[DeprecatedBehaviour]]] = {}


_BEHAVIOUR_OWNERS: dict[int, tuple[DeprecatedBehaviour, str | None]] = {}


def bind_behaviours(listings: Mapping[str, Callable[[], Sequence[DeprecatedBehaviour]]]) -> None:
    """Record each composed plugin's ``deprecations`` (plugin name → its callable), anew."""
    _LISTINGS.clear()
    _LISTINGS.update(listings)
    _BEHAVIOUR_OWNERS.clear()


def behaviour_owner(behaviour: DeprecatedBehaviour) -> str | None:
    """The plugin whose class holds ``behaviour``, or ``None`` for core's.

    Bound by the composition's listings: core's own come first (so no plugin
    can claim one), then each plugin's. One nobody lists, or used outside a
    composition, reads as core's; ``tests/repo`` keeps this workspace's
    behaviours listed.
    """
    key = id(behaviour)
    if key not in _BEHAVIOUR_OWNERS:
        found: str | None = None
        if not any(item is behaviour for item in _core_behaviours()):
            listers = (n for n, items in _listed() if any(item is behaviour for item in items))
            found = next(listers, None)
        _BEHAVIOUR_OWNERS[key] = (behaviour, found)
    return _BEHAVIOUR_OWNERS[key][1]


def _distribution(behaviour: DeprecatedBehaviour) -> str | None:
    """The distribution whose major ``behaviour`` names; ``None`` for core's."""
    plugin = behaviour.plugin
    return None if plugin == _CORE_OWNER else owner(plugin)


def _listed() -> Iterator[tuple[str, list[DeprecatedBehaviour]]]:
    """Each composed plugin's listed behaviours; a listing that fails to load lists none.

    Asked while warning, so a broken plugin listing never turns a warning into a crash.
    """
    for name, listing in _LISTINGS.items():
        try:
            items = list(listing())
        except Exception:
            continue
        yield name, [item for item in items if isinstance(item, DeprecatedBehaviour)]
