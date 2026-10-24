"""The user: typed prompts and the external editor (``untaped.sdk.prompt``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.prompt.editor import run_editor
    from untaped.sdk.prompt.prompts import PromptChoice

__all__ = ["PromptChoice", "run_editor"]

__getattr__, __dir__ = lazy_names(
    globals(), {"editor": ("run_editor",), "prompts": ("PromptChoice",)}
)
