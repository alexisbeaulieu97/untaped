"""Concrete :class:`Prompt` implementation: the command's terminal asks."""

from __future__ import annotations

from collections.abc import Callable

from untaped.sdk import PromptChoice, Terminal
from untaped_awx.domain.suites.suite import VariableSpec


class UiPrompt:
    """Ask for a suite variable on the terminal the command hands down (built on first use)."""

    def __init__(
        self, terminal: Callable[[], Terminal], *, force_non_interactive: bool = False
    ) -> None:
        self._terminal = terminal
        self._force_non_interactive = force_non_interactive

    def is_interactive(self) -> bool:
        if self._force_non_interactive:
            return False
        # Only ``stdin`` matters: stderr being redirected (``2>/dev/null``)
        # is normal log practice and must not silently disable prompts.
        return self._terminal().can_prompt

    def ask(self, spec: VariableSpec) -> str:
        prompt_text = spec.description or spec.name
        terminal = self._terminal()
        if spec.secret:
            return terminal.secret(prompt_text)
        if spec.type == "choice":
            return terminal.select(
                prompt_text,
                [PromptChoice(value=str(choice), label=str(choice)) for choice in spec.choices],
            )
        return terminal.text(prompt_text)
