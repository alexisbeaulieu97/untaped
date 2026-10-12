"""How one invocation writes stderr: its output policy, and where the current one is.

:class:`OutputPolicy` is decided in two phases, both through
:func:`note_format`: from argv before parsing (``note_requested_format``, so a
parse error renders the way ``-f json`` asks), then from the command's own
``--format`` once settings are loaded (``apply_default_format``);
``UNTAPED_DIAGNOSTICS`` overrides both. ``--quiet`` and ``--verbose`` are flags on it,
set by their root-option handlers. It is internal: a plugin that could read
the policy would branch on JSON around the sink.

The policy belongs to the invocation's scope (``untaped.output.sink``). The
scope registers here: in a ``ContextVar``, and in one process-wide ordered set
of live scopes, so a bare thread with an empty context still finds the
invocation (the most recently entered scope still live). Each scope removes
itself by identity when it exits, so scopes that overlap on threads never pop
each other. Its one documented limit: a bare thread that outlives its scope, or
runs while a later scope is live (one test process, many invocations), reaches
whichever scope is current. Code that needs its own invocation from a thread
runs under ``bounded_map`` or copies the context.
"""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from untaped.theme import ThemeSpec

#: Environment variable choosing the stderr format: ``json`` or ``text``.
DIAGNOSTICS_ENV = "UNTAPED_DIAGNOSTICS"
#: Output formats whose stderr diagnostics default to JSON Lines.
STRUCTURED_FORMATS = frozenset({"json", "yaml", "pipe"})


@dataclass(frozen=True)
class OutputPolicy:
    """What the invocation asked of stderr; never in ``untaped.sdk``.

    ``format`` is the ``--format`` the user chose (``None``: the command's
    default), ``forced`` the ``UNTAPED_DIAGNOSTICS`` choice read when the
    policy was built (``None``: follow the format). ``theme`` is the
    ``ui.theme`` text messages render with, resolved once settings load
    (``None``: the default theme); it is left out of the hash, since a
    ``ThemeSpec`` has none.
    """

    format: str | None = None
    forced: Literal["text", "json"] | None = None
    quiet: bool = False
    verbose: bool = False
    theme: ThemeSpec | None = field(default=None, hash=False)

    @property
    def diagnostics(self) -> Literal["text", "json"]:
        """Whether stderr carries text or JSON Lines."""
        if self.forced is not None:
            return self.forced
        return "json" if self.format in STRUCTURED_FORMATS else "text"


def _forced() -> Literal["text", "json"] | None:
    choice = os.environ.get(DIAGNOSTICS_ENV, "").strip().lower()
    if choice == "json":
        return "json"
    if choice == "text":
        return "text"
    return None


def with_format(policy: OutputPolicy, fmt: str | None) -> OutputPolicy:
    """``policy`` with the ``--format`` the invocation chose."""
    return replace(policy, format=fmt)


class Holder(Protocol):
    """What holds a policy: an invocation's scope."""

    policy: OutputPolicy


class _Unscoped:
    """The policy outside every scope (a helper called from a unit test).

    It follows the environment at each read, as stderr did before scopes
    recorded anything; ``--quiet`` and ``--verbose`` set on it persist until
    reset.
    """

    def __init__(self) -> None:
        self._flags = OutputPolicy()

    @property
    def policy(self) -> OutputPolicy:
        return replace(self._flags, forced=_forced())

    @policy.setter
    def policy(self, value: OutputPolicy) -> None:
        self._flags = value


_UNSCOPED = _Unscoped()
_current: ContextVar[Holder | None] = ContextVar("untaped_output_scope", default=None)
_live: list[Holder] = []
_live_lock = threading.Lock()


def enter(holder: Holder) -> object:
    """Make ``holder`` the current scope; returns the token :func:`leave` takes."""
    with _live_lock:
        _live.append(holder)
    return _current.set(holder)


def leave(holder: Holder, token: object) -> None:
    """Remove ``holder`` from the live scopes, by identity, wherever it sits."""
    _current.reset(token)  # type: ignore[arg-type]
    with _live_lock:
        for index in range(len(_live) - 1, -1, -1):
            if _live[index] is holder:
                del _live[index]
                break


def context_holder() -> Holder | None:
    """This context's scope (``None`` on a bare thread or outside every scope)."""
    return _current.get()


def current_holder() -> Holder | None:
    """The current scope: this context's, else the most recently entered live one."""
    holder = _current.get()
    if holder is not None:
        return holder
    with _live_lock:
        return _live[-1] if _live else None


def _holder() -> Holder:
    return current_holder() or _UNSCOPED


def unscoped_policy() -> OutputPolicy:
    """The policy outside every scope: the environment's, with any flag set there.

    A scope entered with no enclosing one starts from it, so ``--quiet`` set
    before an in-process invocation (a test, an embedder) still applies.
    """
    return _UNSCOPED.policy


def current_policy() -> OutputPolicy:
    """The current scope's policy (outside every scope: the environment's)."""
    return _holder().policy


def note_format(fmt: str | None) -> None:
    """Record the ``--format`` the invocation chose on its scope's policy.

    Phase 1 records what argv asks for before parsing; phase 2 what the
    parsed command chose (:func:`with_format`). Outside a scope there is no
    invocation to record it for, and nothing happens.
    """
    holder = context_holder()
    if holder is not None:
        holder.policy = with_format(holder.policy, fmt)


def note_theme(theme: ThemeSpec | None) -> None:
    """Record the theme text messages render with on the scope's policy.

    Set once settings load (phase 2, beside :func:`note_format`; ``None``
    under JSON diagnostics, which render no theme); a nested scope inherits
    it. Outside a scope nothing happens.
    """
    holder = context_holder()
    if holder is not None:
        holder.policy = replace(holder.policy, theme=theme)


def is_verbose() -> bool:
    """Whether ``--verbose`` is active for the current invocation."""
    return current_policy().verbose


def is_quiet() -> bool:
    """Whether ``--quiet`` is active for the current invocation."""
    return current_policy().quiet


type FlagToken = tuple[Holder, OutputPolicy]
"""What a flag handler returns: the scope it changed and its policy before."""


def set_flag(name: Literal["quiet", "verbose"], value: bool) -> FlagToken:
    """Set ``--quiet`` or ``--verbose`` on the current scope's policy."""
    holder = _holder()
    previous = holder.policy
    holder.policy = (
        replace(previous, quiet=value) if name == "quiet" else replace(previous, verbose=value)
    )
    return holder, previous


def reset_flag(name: Literal["quiet", "verbose"], token: FlagToken | None) -> None:
    """Restore the policy ``token`` saved, or clear the flag on the current scope."""
    if token is None:
        set_flag(name, False)
        return
    holder, previous = token
    holder.policy = (
        replace(holder.policy, quiet=previous.quiet)
        if name == "quiet"
        else replace(holder.policy, verbose=previous.verbose)
    )


def enable_quiet(_value: str = "") -> FlagToken:
    """Root-option handler for ``--quiet``/``-q``.

    Mutes commentary: lines the user did not ask for and need not act on
    (progress, ``success`` and ``info``). A preview before a prompt, a stream
    the user asked to watch, and output a flag exists to produce are not
    commentary and print (``info(…, unmuted=True)``). Warnings, failures and
    hints always print, as do prompts and stdout. Takes no value; the
    dispatcher passes a placeholder string that is ignored.
    """
    return set_flag("quiet", True)


def reset_quiet(token: FlagToken | None = None) -> None:
    """Clear or restore ``--quiet`` after one invocation."""
    reset_flag("quiet", token)


__all__ = [
    "DIAGNOSTICS_ENV",
    "STRUCTURED_FORMATS",
    "OutputPolicy",
    "current_policy",
    "enable_quiet",
    "is_quiet",
    "is_verbose",
    "note_format",
    "note_theme",
    "reset_quiet",
    "with_format",
]
