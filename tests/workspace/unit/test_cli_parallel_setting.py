"""CLI tests for the ``workspace.parallel`` setting behind ``sync`` and ``foreach``.

Unset, both commands run ``min(8, 2 x os.cpu_count())`` workers; the setting
overrides that default and ``--parallel`` overrides the setting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import Foreach, SyncWorkspaces
from untaped.capabilities.workspace.cli import app
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


@pytest.fixture
def seen_workers(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the ``parallel`` value each command passes to its use case."""
    seen: list[int] = []
    real_sync = SyncWorkspaces.__call__
    real_foreach = Foreach.__call__

    def _sync(self: SyncWorkspaces, *args: object, parallel: int = 1, **kw: object) -> object:
        seen.append(parallel)
        return real_sync(self, *args, parallel=parallel, **kw)  # type: ignore[arg-type]

    def _foreach(self: Foreach, *args: object, parallel: int = 1, **kw: object) -> object:
        seen.append(parallel)
        return real_foreach(self, *args, parallel=parallel, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(SyncWorkspaces, "__call__", _sync)
    monkeypatch.setattr(Foreach, "__call__", _foreach)
    return seen


@pytest.fixture
def solo(tmp_path: Path, isolate_config: Path) -> Path:
    target = tmp_path / "solo"
    CliInvoker().invoke(app, ["init", "solo", "--path", str(target)])
    return target


def _set_parallel(config: Path, value: int) -> None:
    config.write_text(
        f"active: default\nprofiles:\n  default:\n    workspace:\n      parallel: {value}\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()


def test_parallel_setting_defaults_to_unset() -> None:
    assert WorkspaceSettings().parallel is None


@pytest.mark.usefixtures("solo")
@pytest.mark.parametrize(("cpus", "expected"), [(1, 2), (2, 4), (8, 8), (64, 8)])
@pytest.mark.parametrize("command", [["sync", "solo"], ["foreach", "solo", "true"]])
def test_default_workers_are_min_of_eight_and_twice_the_cpus(
    monkeypatch: pytest.MonkeyPatch,
    seen_workers: list[int],
    command: list[str],
    cpus: int,
    expected: int,
) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: cpus)

    result = CliInvoker().invoke(app, command)

    assert result.exit_code == 0, result.output
    assert seen_workers == [expected]


@pytest.mark.usefixtures("solo")
@pytest.mark.parametrize("command", [["sync", "solo"], ["foreach", "solo", "true"]])
def test_setting_overrides_default_and_flag_overrides_setting(
    monkeypatch: pytest.MonkeyPatch,
    isolate_config: Path,
    seen_workers: list[int],
    command: list[str],
) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    _set_parallel(isolate_config, 3)

    from_setting = CliInvoker().invoke(app, command)
    from_flag = CliInvoker().invoke(app, [*command, "-j", "5"])

    assert (from_setting.exit_code, from_flag.exit_code) == (0, 0), from_flag.output
    assert seen_workers == [3, 5]


@pytest.mark.usefixtures("solo")
def test_setting_above_the_cap_is_clamped(
    monkeypatch: pytest.MonkeyPatch, isolate_config: Path, seen_workers: list[int]
) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    _set_parallel(isolate_config, 16)

    result = CliInvoker().invoke(app, ["sync", "solo", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert seen_workers == [4]
    assert "clamped to 4" in result.stderr
    assert json.loads(result.stdout) == []


def test_parallel_setting_rejects_zero(isolate_config: Path) -> None:
    _set_parallel(isolate_config, 0)

    result = CliInvoker().invoke(app, ["sync", "--all"])

    assert result.exit_code != 0
