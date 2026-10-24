"""The fakes behind the ``terminal`` and ``credentials`` fixtures.

:class:`ScriptedTerminal` is the terminal a test scripts: absent until the
test gives it answers, typed text or screens. :class:`MemoryCredentials` is
the password store every test runs with, so a ``Credential`` never reaches
the machine's own.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Self, cast

from untaped.screen.core import Cancel, Quit, Screen
from untaped.sdk.errors import ConfigError, PromptInterruptedError
from untaped.sdk.testing.screens import ScreenKeys, drive_screen

if TYPE_CHECKING:
    from untaped.runtime.theme import ThemeSpec
    from untaped.screen.picker import PickRequest, PickResult
    from untaped.sdk.network.credentials import Credential, CredentialKey
    from untaped.sdk.prompt.prompts import PromptChoice
    from untaped.sdk.testing.install import Install


class ScriptedTerminal:
    """The terminal a test scripts; absent until the test gives it something to answer.

    ``answers(True, False)`` answers the next yes/no questions in order, and
    ``answers(yes=True)`` every one; ``types("Ada")`` answers the next text
    prompts (and secret ones); ``screens(...)`` answers the next screens:
    each entry is the screen's result, a ``Quit``, a ``Cancel`` (or the class),
    an exception (raised) or ``ScreenKeys`` (replayed through
    ``drive_screen``). ``absent()`` takes the terminal away again. ``asked``
    lists each prompt as ``(kind, message)`` and ``ran`` each screen run. A
    prompt with nothing scripted for it fails the test instead of waiting.
    """

    needs_terminal = False

    def __init__(self) -> None:
        self.present = False
        #: Every prompt the commands asked, as ``(kind, message)``, in order.
        self.asked: list[tuple[str, str]] = []
        #: Every screen the commands ran, in order.
        self.ran: list[Screen[Any, Any]] = []
        self._always: bool | None = None
        self._interrupt = False
        self._confirms: deque[bool] = deque()
        self._texts: deque[str] = deque()
        self._secrets: deque[str] = deque()
        self._selections: deque[Any] = deque()
        self._multiselects: deque[list[Any]] = deque()
        self._picks: deque[PickResult | None] = deque()
        self._screens: deque[object] = deque()

    def answers(self, *answers: bool, yes: bool | None = None) -> Self:
        """Answer the next yes/no questions in order, or every one with ``yes``."""
        self._confirms.extend(answers)
        self._always = yes
        self.present = True
        return self

    def types(self, *texts: str) -> Self:
        """Answer the next text prompts in order (and secret ones, when none is scripted)."""
        self._texts.extend(texts)
        self.present = True
        return self

    def screens(self, *results: object) -> Self:
        """Answer the next screens in order."""
        self._screens.extend(results)
        self.present = True
        return self

    def absent(self) -> Self:
        """No terminal: a command that has to ask exits 2, as under a script."""
        self.present = False
        return self

    # --- what a command's prompts call (PromptBackend) ---------------------------

    def confirm(self, message: str, *, default: bool) -> bool:
        if self._always is not None and not self._confirms:
            self.asked.append(("confirm", message))
            return self._always
        return bool(self._next(self._confirms, "confirm", message))

    def text(self, message: str, *, default: str | None) -> str:
        return str(self._next(self._texts, "text", message))

    def secret(self, message: str, *, confirmation: bool) -> str:
        return str(self._next(self._secrets or self._texts, "secret", message))

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        default: Any | None,
        search: bool,
    ) -> Any:
        return self._next(self._selections, "select", message)

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        defaults: Sequence[Any],
    ) -> list[Any]:
        return list(self._next(self._multiselects, "multiselect", message))

    def pick_many(self, request: PickRequest) -> PickResult | None:
        return cast("PickResult | None", self._next(self._picks, "pick_many", request.heading))

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        self.ran.append(screen)
        entry = self._next(self._screens, "run_screen", screen.title)
        if isinstance(entry, BaseException) or (
            isinstance(entry, type) and issubclass(entry, BaseException)
        ):
            raise entry
        if entry is Cancel:
            return Cancel()
        if isinstance(entry, Quit | Cancel):
            return cast("Quit[R] | Cancel", entry)
        if isinstance(entry, ScreenKeys):
            run = drive_screen(screen, entry, theme=theme, commands="sync")
            if run.outcome is None:
                raise ConfigError(
                    f"the scripted keys did not end screen {screen.title!r}; "
                    "finish them with a key that quits"
                )
            return run.outcome
        return cast("Quit[R]", Quit(entry))

    def _next(self, queue: deque[Any], kind: str, message: str) -> Any:
        self.asked.append((kind, message))
        if self._interrupt:
            raise PromptInterruptedError("prompt cancelled")
        if not queue:
            raise ConfigError(f"no scripted {kind} answer for prompt {message!r}")
        return queue.popleft()


_TERMINAL: ContextVar[ScriptedTerminal | None] = ContextVar(
    "untaped_scripted_terminal", default=None
)


@contextmanager
def scripted_terminal() -> Iterator[ScriptedTerminal]:
    """Make a fresh :class:`ScriptedTerminal` the one ``install`` runs commands with."""
    terminal = ScriptedTerminal()
    token = _TERMINAL.set(terminal)
    try:
        yield terminal
    finally:
        _TERMINAL.reset(token)


def current_terminal() -> ScriptedTerminal | None:
    """The scripted terminal of this test, when it asked for one."""
    return _TERMINAL.get()


class MemoryCredentials:
    """The password store a test runs with: what a ``Credential`` stores and reads.

    ``set(Shop.token, ShopKey(url=...), "s3cret")`` files a secret as
    ``untaped auth login`` would, so the credential reads it back; ``asked``
    lists each secret read, by how the store labels it. ``entries`` maps
    each store entry to its secret and ``labels`` to how a real store would
    list it.
    """

    name = "memory"

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.labels: dict[str, str | None] = {}
        self.asked: list[str] = []
        #: The installs of this test, newest last: a plugin class's credential is named by one.
        self.installs: list[Install] = []

    def set(self, credential: Credential[Any], key: CredentialKey, secret: str) -> None:
        """File ``secret`` as ``credential``'s for ``key``, under the test's latest install."""
        from untaped.sdk.run import NoRunError, current_run  # noqa: PLC0415

        try:
            current_run()
        except NoRunError:
            if not self.installs:
                raise RuntimeError(
                    "install the credential's plugin before setting it: "
                    "its install names the plugin it belongs to"
                ) from None
            with self.installs[-1].running():
                credential.store(key, token=secret)
            return
        credential.store(key, token=secret)

    # --- what the credential store calls (ReadableStore) -------------------------

    def preflight(self) -> None:
        """A memory store always works."""

    def save(self, entry: str, token: str, *, label: str | None = None) -> None:
        self.entries[entry] = token
        self.labels[entry] = label

    def read(self, entry: str) -> str:
        if entry not in self.entries:
            raise ConfigError(f"the in-memory store has no entry {entry}")
        self.asked.append(self.labels.get(entry) or entry)
        return self.entries[entry]

    def delete(self, entry: str) -> None:
        if entry not in self.entries:
            raise ConfigError(f"the in-memory store has no entry {entry}")
        del self.entries[entry]
        self.labels.pop(entry, None)

    def gone(self, entry: str) -> bool:
        return entry not in self.entries

    def describe(self, entry: str) -> str:
        return f"memory ({entry})"


__all__ = ["MemoryCredentials", "ScriptedTerminal", "current_terminal", "scripted_terminal"]
