from __future__ import annotations

from pathlib import Path

import pytest

from untaped_core.errors import TemplateRenderingError
from untaped_core.template_renderer import render_template


def test_render_template_success(tmp_path: Path) -> None:
    template = tmp_path / "example.yml"
    template.write_text("name: {{ value }}\n", encoding="utf-8")

    rendered = render_template(template, {"value": "demo"})

    assert rendered.strip() == "name: demo"


def test_render_template_missing_variable(tmp_path: Path) -> None:
    template = tmp_path / "missing.yml"
    template.write_text("name: {{ value }}\n", encoding="utf-8")

    with pytest.raises(TemplateRenderingError):
        render_template(template, {})
