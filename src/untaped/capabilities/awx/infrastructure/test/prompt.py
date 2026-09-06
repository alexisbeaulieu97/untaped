"""Concrete :class:`Prompt` implementation backed by core UI prompts."""

from __future__ import annotations

import sys

from untaped.api import PromptChoice, ui_context
from untaped.capabilities.awx.domain.test_suite import VariableSpec


class UiPrompt:
    def __init__(self, *, force_non_interactive: bool = False) -> None:
        self._force_non_interactive = force_non_interactive

    def is_interactive(self) -> bool:
        if self._force_non_interactive:
            return False
        # Only ``stdin`` matters: stderr being redirected (``2>/dev/null``)
        # is normal log practice and must not silently disable prompts.
        return sys.stdin.isatty()

    def ask(self, spec: VariableSpec) -> str:
        prompt_text = spec.description or spec.name
        ui = ui_context(strict=False)
        if spec.secret:
            return ui.secret(prompt_text)
        if spec.type == "choice":
            return ui.select(
                prompt_text,
                [PromptChoice(value=str(choice), label=str(choice)) for choice in spec.choices],
            )
        return ui.text(prompt_text)
