"""The terminal handle a command asks through (``self.ctx.terminal``), as a type."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol

from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from untaped.screen.core import Screen
    from untaped.screen.picker import PickRequest, PickResult
    from untaped.sdk.prompt.prompts import PromptChoice


class Terminal(Protocol):
    """The terminal a command asks through (``self.ctx.terminal``): prompts, pickers and screens.

    The run builds the one a command gets. Each prompt fails with a usage
    error when nothing can answer it, ``can_prompt`` tells first, and a
    cancelled prompt ends the command like any cancel. What a command prints
    to stdout goes through its ``Output``.
    """

    @property
    def can_prompt(self) -> bool: ...

    def confirm(self, message: str, *, default: bool = False) -> bool: ...

    def text(self, message: str, *, default: str | None = None, required: bool = True) -> str: ...

    def secret(self, message: str, *, confirmation: bool = False, required: bool = True) -> str: ...

    def select[T](
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None = None,
        search: bool = False,
    ) -> T: ...

    def pick_many(self, request: PickRequest) -> PickResult: ...

    @experimental
    def run[M, R](self, screen: Screen[M, R]) -> R: ...

    @experimental
    def require_screen_terminal(self, *, command: str, alternative: str) -> None: ...

    def terminal(
        self, *, refusal: str = "interactive input requires a terminal"
    ) -> AbstractContextManager[Terminal]: ...
