"""A plugin class's doctor checks: their ids come from the attribute and the run."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import DoctorCheck, DoctorResult, Plugin
from untaped.sdk._run import run_scope
from untaped.sdk.health.doctor import executable_check


class DemoSettings(BaseModel):
    pass


def _fine(_ctx: object) -> DoctorResult:
    return DoctorResult(id="demo.repo-store", ok=True, detail="fine")


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    repo_store = DoctorCheck(title="repo store", run=_fine)


def test_a_plugin_classs_check_takes_a_kebab_id(tmp_path: Path) -> None:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")):
        assert Demo.repo_store.id == "demo.repo-store"
        assert repr(Demo.repo_store).startswith("DoctorCheck('DoctorCheck created in ")


def test_a_check_records_the_module_creating_it() -> None:
    assert Demo.repo_store.module == __name__
    assert executable_check("demo.git", "git", purpose="cloning").module == __name__


def test_the_11x_spelling_keeps_its_id() -> None:
    assert DoctorCheck("git.repo-store", title="git store", run=_fine).id == "git.repo-store"
    shell = DoctorCheck("config", title="config", run=_fine)
    assert (shell.id, repr(shell)) == ("config", "DoctorCheck('config', title='config')")
    assert DoctorCheck("a.", title="t", run=_fine).id == "a."
