"""A plugin class's doctor checks and migrations: their ids come from the attribute and the run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import DirMigration, DoctorCheck, DoctorResult, Plugin
from untaped.sdk.health.doctor import executable_check
from untaped.sdk.run import Composition, Run, run_scope


class DemoSettings(BaseModel):
    pass


def _fine(_ctx: object) -> DoctorResult:
    return DoctorResult(id="demo.repo-store", ok=True, detail="fine")


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    repo_store = DoctorCheck(title="repo store", run=_fine)
    greetings = DirMigration(title="greetings", preview=lambda *_: [], apply=lambda *_: [])


def _run(tmp_path: Path) -> Run:
    return Run(
        home=tmp_path,
        composition=Composition(packages={__name__.partition(".")[0]: "demo"}),
        picks=lambda _section, model: model(),
        diag=None,  # type: ignore[arg-type]
        terminal=lambda: pytest.fail("no terminal in this test"),
    )


def test_a_plugin_classs_check_and_migration_take_kebab_ids(tmp_path: Path) -> None:
    with run_scope(_run(tmp_path)):
        assert Demo.repo_store.id == "demo.repo-store"
        assert Demo.greetings.id == "demo.greetings"
        assert repr(Demo.repo_store).startswith("DoctorCheck('DoctorCheck created in ")


def test_a_check_and_a_migration_record_the_module_creating_them() -> None:
    assert Demo.repo_store.module == __name__
    assert Demo.greetings.module == __name__
    assert executable_check("demo.git", "git", purpose="cloning").module == __name__


def test_the_11x_spelling_keeps_its_id() -> None:
    assert DoctorCheck("git.repo-store", title="git store", run=_fine).id == "git.repo-store"
    shell = DoctorCheck("config", title="config", run=_fine)
    assert (shell.id, repr(shell)) == ("config", "DoctorCheck('config', title='config')")
    assert DoctorCheck("a.", title="t", run=_fine).id == "a."
    migration = DirMigration(
        "hello.greetings", title="t", preview=lambda *_: [], apply=lambda *_: []
    )
    assert (migration.plugin, migration.name, migration.id) == (
        "hello",
        "greetings",
        "hello.greetings",
    )
