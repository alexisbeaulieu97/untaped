from __future__ import annotations

import time
from pathlib import Path

from untaped_core.template_renderer import render_template


def test_template_rendering_under_50ms(tmp_path: Path) -> None:
    template = tmp_path / "template.yml"
    template.write_text("name: {{ value }}\n" * 10, encoding="utf-8")

    start = time.perf_counter()
    for _ in range(20):
        render_template(template, {"value": "demo"})
    duration = (time.perf_counter() - start) / 20

    assert duration < 0.05
