"""A plugin class's executable and online checks: their ids and titles come from the run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import ExecutableCheck, OnlineCheck, Plugin, PluginContext
from untaped.sdk.run import run_scope


class DemoSettings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    missing = ExecutableCheck("no-such-program-untaped", purpose="cloning")
    probe = OnlineCheck(probe=lambda: "signed in as ada")


@pytest.fixture(autouse=True)
def _run(tmp_path: Path) -> object:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")) as active:
        yield active


def test_an_executable_check_warns_naming_what_needs_the_program() -> None:
    result = Demo.missing.run(PluginContext(settings=DemoSettings()))
    assert (result.id, result.ok, result.warn) == ("demo.missing", True, True)
    assert "`no-such-program-untaped` not found on PATH; cloning will fail" in result.detail
    assert Demo.missing.title == "no-such-program-untaped on PATH"
    assert Demo.missing.module == __name__


def test_an_online_check_probes_the_plugins_own_section() -> None:
    assert (Demo.probe.id, Demo.probe.title, Demo.probe.online) == (
        "demo.probe",
        "demo API reachable",
        True,
    )
    result = Demo.probe.run(PluginContext(settings=None))
    assert (result.id, result.detail) == ("demo.probe", "skipped: settings are invalid")
    assert OnlineCheck(probe=str, title="Demo API").title == "Demo API"
