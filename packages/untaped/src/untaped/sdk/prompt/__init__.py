"""The user: typed prompts and the external editor (``untaped.sdk.prompt``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.prompt.editor import run_editor
    from untaped.sdk.prompt.prompts import PromptChoice

__all__ = ["PromptChoice", "run_editor"]

if not TYPE_CHECKING:  # a type checker reads the imports above; a run reads the table
    __getattr__, __dir__ = lazy_names(
        globals(), {"editor": ("run_editor",), "prompts": ("PromptChoice",)}
    )
