"""Recipe placeholder rendering for template bodies and path-bearing fields.

Both come from the values plugin. Path fields render through its strict
``render``, which refuses secret and structured inputs. Template bodies (and
the hook helpers) render through the file ``renderer_file()`` names, loaded by
path exactly as the hook worker loads it in a pack's environment, so built-in
hooks, external hook workers and the planner share one implementation.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from functools import cache
from types import ModuleType
from typing import TYPE_CHECKING

from untaped_values.api import render, renderer_file

if TYPE_CHECKING:
    from untaped_recipe.domain.recipe import InputSpec

__all__ = ["render_field", "render_template"]


def render_template(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    """Render ``{{ name }}`` placeholders in a template body from resolved recipe inputs."""
    rendered: str = _renderer().render_template(template, inputs, unknown_tokens=unknown_tokens)
    return rendered


def render_field(
    text: str,
    *,
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
    field: str,
) -> str:
    """Render a path-bearing recipe field: strict bare tokens, no secret or structured input."""
    return render(text, values, specs, field=field)


@cache
def _renderer() -> ModuleType:
    """The values plugin's renderer file, loaded by path under a private module name."""
    path = renderer_file()
    spec = importlib.util.spec_from_file_location("_untaped_recipe_renderer", path)
    if spec is None or spec.loader is None:  # pragma: no cover - the file ships with values
        raise ImportError(f"cannot load the renderer at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
