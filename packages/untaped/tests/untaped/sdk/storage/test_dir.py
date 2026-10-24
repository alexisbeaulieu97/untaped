"""Dir: a place of a plugin's own, in its folder or at a path setting, with its locks."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from filelock import FileLock
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.runtime.composition import PluginEntry, compose
from untaped.sdk import ConfigError, Dir, ErrorCategory, Plugin, UntapedError, key
from untaped.sdk.plugin_records import InstalledPlugin
from untaped.sdk.run import Composition, Run, run_scope


class Settings(BaseModel):
    checkouts_dir: Path | None = None


class Demo(Plugin):
    """Demo."""

    settings = Settings
    notes = Dir(wait=timedelta(0))
    checkouts = Dir(at=key(Settings).checkouts_dir)
    index = Dir(layout=4, file="index.sqlite3")
    images = Dir(layout=2)


@pytest.fixture
def picked(tmp_path: Path) -> dict[str, object]:
    return {"checkouts_dir": tmp_path / "user" / "checkouts"}


@pytest.fixture(autouse=True)
def run(tmp_path: Path, picked: dict[str, object]) -> Iterator[Run]:
    owning = run_owning(tmp_path / "home", __name__.partition(".")[0], "demo")
    active = replace(
        owning,
        composition=replace(owning.composition, owners={Settings: "demo"}),
        picks=lambda _section, model: model.model_validate(picked),
    )
    with run_scope(active):
        yield active


def test_a_place_is_a_folder_in_the_plugins_folder(tmp_path: Path) -> None:
    where = tmp_path / "home" / "plugins" / "demo" / "notes"
    assert Demo.notes.path() == where
    assert not where.exists()
    assert Demo.notes.open() == where
    assert where.is_dir()


def test_at_names_a_path_setting(tmp_path: Path) -> None:
    assert Demo.checkouts.path() == tmp_path / "user" / "checkouts"
    assert Demo.checkouts.open().is_dir()


def test_at_expands_the_users_folder(picked: dict[str, object]) -> None:
    picked["checkouts_dir"] = "~/checkouts"
    assert Demo.checkouts.path() == Path("~/checkouts").expanduser()


def test_an_unset_path_setting_says_so(picked: dict[str, object]) -> None:
    picked["checkouts_dir"] = None
    with pytest.raises(ConfigError, match=r"holds None, not a path"):
        Demo.checkouts.path()


def test_a_numbered_file_is_one_file_per_layout(tmp_path: Path) -> None:
    folder = tmp_path / "home" / "plugins" / "demo" / "index"
    assert Demo.index.path() == folder / "index.v4.sqlite3"
    assert Demo.index.open() == folder / "index.v4.sqlite3"
    assert folder.is_dir()


def test_a_numbered_folder_opens_its_layout_and_prunes_without_raising(tmp_path: Path) -> None:
    folder = tmp_path / "home" / "plugins" / "demo"
    assert Demo.images.path() == folder / "images.v2"
    Demo.images.open()
    Demo.images.prune()
    Demo.notes.prune()  # a place with no layouts has nothing to prune
    assert Demo.images.path() == folder / "images.v2"


def test_an_unnumbered_file_is_the_file_in_the_place(tmp_path: Path) -> None:
    class Single(Plugin):
        """Single."""

        settings = Settings
        log = Dir(file="log.txt")

    assert Single.log.path() == tmp_path / "home" / "plugins" / "demo" / "log" / "log.txt"
    assert Single.log.open().parent.is_dir()
    assert not Single.log.path().exists()


def test_a_place_it_cannot_create_says_so(tmp_path: Path) -> None:
    (tmp_path / "home" / "plugins").mkdir(parents=True)
    (tmp_path / "home" / "plugins" / "demo").write_text("a file, not a folder")
    with pytest.raises(ConfigError, match="cannot create"):
        Demo.notes.open()


def test_locked_holds_a_thing_or_the_whole_place(tmp_path: Path) -> None:
    folder = tmp_path / "home" / "plugins" / "demo"
    with Demo.notes.locked("ada"), Demo.notes.locked(), Demo.notes.locked("ada"):
        assert Demo.notes.lock_path() == folder / "notes.lock"
        assert Demo.notes.lock_path("ada").parent == folder / "notes.locks"
        assert Demo.notes.lock_path("ada").exists()
        assert Demo.notes.lock_path("ada") != Demo.notes.lock_path("bob")


def test_another_holder_makes_a_thing_busy() -> None:
    path = Demo.notes.lock_path("ada")
    path.parent.mkdir(parents=True)
    with FileLock(str(path)):
        with Demo.notes.try_locked("ada") as held:
            assert not held
        with Demo.notes.try_locked("bob") as held:
            assert held
        with (
            pytest.raises(
                UntapedError, match=r"demo\.notes 'ada' is busy \(another untaped process\)"
            ) as caught,
            Demo.notes.locked("ada"),
        ):
            pytest.fail("held while another process holds it")
        assert caught.value.category == ErrorCategory.UNAVAILABLE


def test_locked_needs_a_declared_wait() -> None:
    with pytest.raises(ConfigError, match="declares no wait="), Demo.checkouts.locked():
        pytest.fail("held without a wait")
    with Demo.checkouts.try_locked() as held:
        assert held


def test_a_things_name_is_a_string() -> None:
    with pytest.raises(TypeError, match="takes a thing's name"), Demo.notes.try_locked(""):
        pytest.fail("held without a name")


def test_a_plugins_own_lock_comes_before_its_providers(run: Run) -> None:
    class Workspace(Plugin):
        """Workspace."""

        settings = Settings
        checkouts = Dir(wait=timedelta(0))

    rows = (
        InstalledPlugin(name="git", status="ready", distribution="untaped-git", version="1"),
        InstalledPlugin(
            name="workspace",
            status="ready",
            distribution="untaped-workspace",
            version="1",
            requires=("untaped-git",),
        ),
    )
    package = __name__.partition(".")[0]

    def as_plugin(plugin: str) -> Run:
        return replace(
            run, composition=Composition(packages={package: plugin}, installed=lambda: rows)
        )

    with (
        run_scope(as_plugin("workspace")),
        Workspace.checkouts.locked("ada"),
        run_scope(as_plugin("git")),
        Demo.notes.locked("ada"),
    ):
        pass  # the provider's lock inside the plugin's own: the order holds
    with (
        run_scope(as_plugin("git")),
        Demo.notes.locked("ada"),
        pytest.raises(UntapedError, match=r"workspace requires git") as caught,
        run_scope(as_plugin("workspace")),
        Workspace.checkouts.locked("ada"),
    ):
        pytest.fail("the plugin's own lock taken inside its provider's")
    assert "workspace.checkouts 'ada' is taken inside git.notes 'ada'" in str(caught.value)


@pytest.mark.parametrize("name", ["cached", "state", "untaped"])
def test_composition_refuses_a_name_core_keeps(name: str) -> None:
    holder = type(
        name.title(), (Plugin,), {"__doc__": "Reserved.", "settings": Settings, name: Dir()}
    )
    declaration = compose([PluginEntry("demo", f"{__name__}:{holder.__name__}", holder)])
    [quarantined] = declaration.quarantined
    assert [p.rule for p in quarantined.problems] == ["block-name"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"at": "checkouts_dir"}, "at takes a path setting"),
        ({"layout": 0}, "layout takes an integer from 1"),
        ({"layout": True}, "layout takes an integer from 1"),
        ({"file": "a/b"}, "file takes one file name"),
        ({"file": ""}, "file takes one file name"),
        ({"wait": 10}, "wait takes a timedelta"),
    ],
)
def test_options_are_checked_where_declared(options: dict[str, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        Dir(**options)  # type: ignore[arg-type]


def test_it_reads_as_written() -> None:
    assert repr(Demo.index) == f"Dir('Dir created in {__name__}', layout=4, file='index.sqlite3')"
