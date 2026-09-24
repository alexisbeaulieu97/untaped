"""Workspace composition checks: the deprecated ``show`` alias and state-backed settings."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.workspace import SPEC
from untaped.settings import get_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    bootstrap._clear_for_tests()
    get_settings.cache_clear()
    yield cfg
    bootstrap._clear_for_tests()
    get_settings.cache_clear()


def _root() -> App:
    return bootstrap.build_root_app(builtins=(SPEC,), externals=())  # type: ignore[return-value]


def test_show_is_a_deprecated_alias_of_get(_isolate: Path, tmp_path: Path) -> None:
    root = _root()
    invoker = CliInvoker()
    invoker.invoke(root.meta, ["workspace", "init", "prod", "--path", str(tmp_path / "ws")])

    result = invoker.invoke(root.meta, ["workspace", "show", "-w", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert "warning: `show` is deprecated" in result.stderr
    assert "use `get`" in result.stderr
    assert json.loads(result.stdout)[0]["workspace"] == "prod"


def test_state_registry_round_trips_through_the_list_command(
    _isolate: Path, tmp_path: Path
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    _isolate.write_text(
        f"workspace:\n  workspaces:\n    - name: prod\n      path: {target}\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["workspace", "list", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["prod"]


def test_state_field_is_not_settable_via_config(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "set", "workspace.workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in result.output
