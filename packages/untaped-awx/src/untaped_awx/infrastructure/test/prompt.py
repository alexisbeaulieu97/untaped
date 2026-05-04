"""Concrete :class:`Prompt` implementation backed by Typer."""

from __future__ import annotations

import sys

import typer

from untaped_awx.domain.test_suite import VariableSpec


class TyperPrompt:
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
        # ``err=True`` routes the prompt to stderr so commands that
        # render data on stdout (``--format json > out.json``) keep that
        # stream clean. Same convention as the rest of the CLI: stdout
        # is data-only; stderr is logs / prompts / progress.
        kwargs: dict[str, object] = {
            "hide_input": spec.secret,
            "type": str,
            "err": True,
        }
        if spec.choices:
            joined = "/".join(str(c) for c in spec.choices)
            prompt_text = f"{prompt_text} [{joined}]"
        return typer.prompt(prompt_text, **kwargs)  # type: ignore[no-any-return,arg-type]
