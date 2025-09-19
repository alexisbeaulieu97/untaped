from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_template(template_path: str | Path, variables: Mapping[str, Any]) -> str:
    path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(path.parent)), undefined=StrictUndefined, autoescape=False
    )
    template = env.get_template(path.name)
    return template.render(**variables)
