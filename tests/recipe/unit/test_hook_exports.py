"""Tests for hook export discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.capabilities.recipe.domain.hook_exports import hook_exports_from_source
from untaped.capabilities.recipe.infrastructure.pack_files import hook_exports


@pytest.mark.parametrize(
    ("src", "exports"),
    [
        pytest.param(
            "def transform(content, **kw):\n    return content\n", {"transform"}, id="one"
        ),
        pytest.param(
            "def transform(c, **kw):\n    return c\n\ndef validate(**kw):\n    return None\n",
            {"transform", "validate"},
            id="dual",
        ),
        pytest.param("def helper():\n    def transform():\n        pass\n", set(), id="nested"),
        pytest.param("async def validate(**kw):\n    return None\n", {"validate"}, id="async"),
    ],
)
def test_hook_exports_are_top_level_verb_functions(src: str, exports: set[str]) -> None:
    assert hook_exports_from_source(src) == frozenset(exports)


def test_file_variant_raises_with_path_on_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "hook.py"
    bad.write_text("def transform(:\n", encoding="utf-8")

    with pytest.raises(ValueError, match=str(bad)):
        hook_exports(bad)
