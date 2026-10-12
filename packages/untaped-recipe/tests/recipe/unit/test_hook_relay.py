"""A hook's own output is relayed as is: an ``error:`` line in it is not a failure."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from untaped.testing import CliInvoker
from untaped_recipe.builtins.registry import BUILTIN_HOOKS, BuiltinHook
from untaped_recipe.cli import app


def test_a_successful_hook_that_prints_error_still_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = ModuleType("chatty")

    def transform(content: str, **_kwargs: object) -> str:
        print("error: only the hook's own log line")
        return content + "!"

    module.transform = transform  # type: ignore[attr-defined]
    hook = BuiltinHook(module=module, exports=frozenset({"transform"}))
    monkeypatch.setitem(BUILTIN_HOOKS, "chatty", hook)
    (tmp_path / "local.txt").write_text("start", encoding="utf-8")

    result = CliInvoker().invoke(
        app, ["hooks", "run", "chatty", "--target", str(tmp_path), "--file", "local.txt"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert "error: only the hook's own log line" in result.stderr
