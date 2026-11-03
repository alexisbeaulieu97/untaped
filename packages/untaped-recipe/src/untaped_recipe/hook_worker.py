"""Stable import path for the hook worker's helpers (used by scaffolded pack tests)."""

from untaped_recipe._worker import helpers as _helpers
from untaped_recipe._worker.helpers import dump_yaml, load_yaml
from untaped_recipe._worker.hook_worker import handle_request
from untaped_recipe.domain.templates import render_template


class HookHelpers(_helpers.HookHelpers):
    """The ``helpers`` a hook receives, rendering with the values plugin's renderer."""

    def __init__(self) -> None:
        super().__init__(render_template)


__all__ = ["HookHelpers", "dump_yaml", "handle_request", "load_yaml", "render_template"]
