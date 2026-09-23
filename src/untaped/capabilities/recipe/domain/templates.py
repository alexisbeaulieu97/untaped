"""Recipe placeholder rendering for template content and path-bearing fields.

The renderer itself lives in the stdlib-only hook helpers module so built-in
hooks, external hook workers, and the planner share one implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from untaped.capabilities.recipe._worker.helpers import (
    bare_token_name,
    render_template,
    structured_render_error,
    template_tokens,
)

if TYPE_CHECKING:
    from untaped.capabilities.recipe.domain.recipe import InputSpec

__all__ = ["render_field", "render_template"]


def render_field(
    text: str,
    *,
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
    field: str,
) -> str:
    """Render a path-bearing recipe field using strict bare input tokens."""
    for token in template_tokens(text):
        name = bare_token_name(token)
        spec = None if name is None else specs.get(name)
        if name is None or spec is None:
            continue
        if spec.sensitive:
            raise ValueError(f"sensitive input {name!r} cannot be used in path field {field!r}")
        if spec.type in {"list", "dict"}:
            raise ValueError(structured_render_error(name))
    return render_template(text, values, unknown_tokens="error")
