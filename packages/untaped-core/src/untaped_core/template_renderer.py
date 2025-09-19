"""Wrapper helpers around Jinja2 rendering with strict error handling."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError, UndefinedError

from .errors import TemplateRenderingError


def render_template(template_path: str | Path, variables: Mapping[str, Any] | None = None) -> str:
    """Render ``template_path`` with ``variables`` using strict undefined checks."""

    path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(path.parent)),
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    try:
        template = env.get_template(path.name)
        return template.render(**(variables or {}))
    except UndefinedError as exc:  # pragma: no cover - exercised via integration tests
        raise TemplateRenderingError(
            f"Missing template variable while rendering '{path}': {exc}"
        ) from exc
    except TemplateError as exc:  # pragma: no cover - difficult to reproduce deterministically
        raise TemplateRenderingError(
            f"Failed to render template '{path}': {exc}"
        ) from exc
