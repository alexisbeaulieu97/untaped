"""Dir: a place of a plugin's own, in its folder or at a path setting, with its locks."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from filelock import FileLock
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.runtime.composition import PluginEntry, compose
from untaped.sdk import ConfigError, Dir, ErrorCategory, Lock, Plugin, UntapedError, key
from untaped.sdk._run import Composition, Run, run_scope
from untaped.sdk.plugin_records import InstalledPlugin
from untaped.sdk.storage.holds import Refused


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
    assert Demo.notes.path == where
    assert not where.exists()
    assert Demo.notes.open() == where
    assert where.is_dir()


def test_at_names_a_path_setting(tmp_path: Path) -> None:
    assert Demo.checkouts.path == tmp_path / "user" / "checkouts"
    assert Demo.checkouts.open().is_dir()


def test_at_expands_the_users_folder(picked: dict[str, object]) -> None:
    picked["checkouts_dir"] = "~/checkouts"
    assert Demo.checkouts.path == Path("~/checkouts").expanduser()


def test_an_optional_setting_left_unset_is_the_default_place(
    tmp_path: Path, picked: dict[str, object]
) -> None:
    picked["checkouts_dir"] = None
    assert Demo.checkouts.path == tmp_path / "home" / "plugins" / "demo" / "checkouts"


@pytest.mark.parametrize(
    ("value", "shown"), [("", "."), ("relative/checkouts", "relative/checkouts"), ("/", "/")]
)
def test_a_setting_that_names_no_absolute_folder_says_so(
    picked: dict[str, object], value: object, shown: str
) -> None:
    picked["checkouts_dir"] = value
    said = f"demo.checkouts: the setting demo.checkouts_dir holds '{shown}', not an absolute"
    with pytest.raises(ConfigError, match=re.escape(said)):
        Demo.checkouts.open()


def test_a_numbered_file_is_one_file_per_layout_in_the_place(tmp_path: Path) -> None:
    folder = tmp_path / "home" / "plugins" / "demo" / "index"
    assert Demo.index.path == folder / "index.v4.sqlite3"
    assert Demo.index.open() == folder / "index.v4.sqlite3"
    assert folder.is_dir()
    assert not Demo.index.path.exists()  # the plugin writes the file


def test_a_numbered_folder_is_created_and_held_for_the_process(tmp_path: Path) -> None:
    folder = tmp_path / "home" / "plugins" / "demo" / "images"
    assert Demo.images.path == folder / "images.v2"
    assert Demo.images.open() == folder / "images.v2"
    assert (folder / "images.v2").is_dir()
    assert (folder / "images.v2.inuse").exists()
    assert Demo.images.open() == folder / "images.v2"


def test_a_numbered_place_at_a_setting_numbers_inside_the_users_folder(tmp_path: Path) -> None:
    class Numbered(Plugin):
        """Numbered."""

        settings = Settings
        index = Dir(at=key(Settings).checkouts_dir, layout=3, file="index.sqlite3")
        clones = Dir(at=key(Settings).checkouts_dir, layout=2)

    folder = tmp_path / "user" / "checkouts"
    assert Numbered.index.path == folder / "index.v3.sqlite3"
    assert Numbered.clones.open() == folder / "clones.v2"
    assert sorted(each.name for each in (tmp_path / "user").iterdir()) == ["checkouts"]


def test_an_unnumbered_file_is_the_file_in_the_place(tmp_path: Path) -> None:
    class Single(Plugin):
        """Single."""

        settings = Settings
        log = Dir(file="log.txt")

    assert Single.log.path == tmp_path / "home" / "plugins" / "demo" / "log" / "log.txt"
    assert Single.log.open().parent.is_dir()
    assert not Single.log.path.exists()


@pytest.mark.parametrize(
    "place",
    [Dir(layout=2), Dir(items=str, release=lambda ask: Refused(reason="no"), moved=None)],
    ids=["numbered", "items"],
)
def test_composition_refuses_a_store_place_a_store_cannot_name(place: Dir) -> None:
    class Shouty(Plugin):
        """Shouty."""

        settings = Settings
        Index = place

    declaration = compose([PluginEntry("demo", f"{__name__}:Shouty", Shouty)])
    [quarantined] = declaration.quarantined
    assert [(p.rule, p.detail) for p in quarantined.problems] == [
        ("block-shape", "a Dir with layout= or items= is named [a-z][a-z0-9_]*, not 'Index'")
    ]


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


class Workspace(Plugin):
    """Workspace: requires git, played by Demo."""

    settings = Settings
    checkouts = Dir(wait=timedelta(0))
    sync = Lock(wait=timedelta(0))


_ROWS = (
    InstalledPlugin(name="git", status="ready", distribution="untaped-git", version="1"),
    InstalledPlugin(
        name="workspace",
        status="ready",
        distribution="untaped_Workspace",  # compared by canonical name
        version="1",
        requires=("untaped-git",),
    ),
)


@pytest.fixture
def as_plugin(run: Run) -> Callable[[str], AbstractContextManager[Run]]:
    package = __name__.partition(".")[0]

    def scope(plugin: str) -> AbstractContextManager[Run]:
        composition = Composition(packages={package: plugin}, installed=lambda: _ROWS)
        return run_scope(replace(run, composition=composition))

    return scope


def test_a_plugins_own_lock_comes_before_its_providers(
    as_plugin: Callable[[str], AbstractContextManager[Run]],
) -> None:
    with (
        as_plugin("workspace"),
        Workspace.checkouts.locked("ada"),
        as_plugin("git"),
        Demo.notes.locked("ada"),
    ):
        pass  # the provider's lock inside the plugin's own: the order holds
    with (
        as_plugin("git"),
        Demo.notes.locked("ada"),
        pytest.raises(UntapedError, match=r"workspace requires git") as caught,
        as_plugin("workspace"),
        Workspace.checkouts.locked("ada"),
    ):
        pytest.fail("the plugin's own lock taken inside its provider's")
    assert "workspace.checkouts 'ada' is taken inside git.notes 'ada'" in str(caught.value)
    assert caught.value.category != ErrorCategory.UNAVAILABLE


def test_the_order_holds_for_a_lock_and_try_locked_raises_it(
    as_plugin: Callable[[str], AbstractContextManager[Run]],
) -> None:
    with (
        as_plugin("git"),
        Demo.notes.locked(),
        pytest.raises(UntapedError, match=r"workspace\.sync is taken inside git\.notes"),
        as_plugin("workspace"),
        Workspace.sync.try_locked(),
    ):
        pytest.fail("try_locked yielded instead of raising")


_WITH_CORE = (
    InstalledPlugin(name="untaped", status="ready", distribution="untaped", version="1", root=True),
    InstalledPlugin(
        name="workspace",
        status="ready",
        distribution="untaped-workspace",
        version="1",
        requires=("untaped",),
    ),
)


def test_a_plugin_s_lock_inside_one_of_core_s_keeps_today_s_order(run: Run) -> None:
    """Core (the root) is every plugin's requirement, not a provider the order is about."""
    package = __name__.partition(".")[0]

    def as_plugin(plugin: str) -> AbstractContextManager[Run]:
        composition = Composition(packages={package: plugin}, installed=lambda: _WITH_CORE)
        return run_scope(replace(run, composition=composition))

    with (
        as_plugin("untaped"),
        Demo.notes.locked("ada"),
        as_plugin("workspace"),
        Workspace.checkouts.locked("ada"),
    ):
        pass  # not "workspace requires untaped"


def test_taking_again_a_lock_the_thread_holds_is_never_out_of_order(
    as_plugin: Callable[[str], AbstractContextManager[Run]],
) -> None:
    with (
        as_plugin("workspace"),
        Workspace.checkouts.locked("ada"),
        as_plugin("git"),
        Demo.notes.locked("ada"),
        as_plugin("workspace"),
        Workspace.checkouts.locked("ada"),
    ):
        pass


def test_locks_let_go_out_of_order_leave_none_behind(
    as_plugin: Callable[[str], AbstractContextManager[Run]],
) -> None:
    with as_plugin("git"):
        first, second = Demo.notes.locked("a"), Demo.notes.locked("b")
        first.__enter__()
        second.__enter__()
        first.__exit__(None, None, None)
        second.__exit__(None, None, None)
    with as_plugin("workspace"), Workspace.checkouts.locked("ada"):
        pass  # no git lock is held any more, so none is out of order


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


def test_a_dir_bound_where_it_is_declared_needs_no_plugin_class(tmp_path: Path) -> None:
    bound = Dir()._bound_to("acme", "notes")
    assert (bound.plugin, bound.name, bound.where) == ("acme", "notes", "acme.notes")
    assert bound.path == tmp_path / "home" / "plugins" / "acme" / "notes"


def test_composition_takes_a_bound_dir_held_as_it_is_bound() -> None:
    bound = Dir()._bound_to("demo", "notes")
    holder = type("Bound", (Plugin,), {"__doc__": "Bound.", "settings": Settings, "notes": bound})
    declaration = compose([PluginEntry("demo", f"{__name__}:Bound", holder)])
    assert declaration.quarantined == ()


@pytest.mark.parametrize(("plugin", "attribute"), [("demo", "other"), ("acme", "notes")])
def test_composition_refuses_a_holder_a_bound_dir_disagrees_with(
    plugin: str, attribute: str
) -> None:
    bound = Dir()._bound_to(plugin, attribute)
    holder = type("Bound", (Plugin,), {"__doc__": "Bound.", "settings": Settings, "notes": bound})
    declaration = compose([PluginEntry("demo", f"{__name__}:Bound", holder)])
    [quarantined] = declaration.quarantined
    assert [p.rule for p in quarantined.problems] == ["block-name"]
    assert (
        f"is bound to {plugin}.{attribute} where it is declared" in quarantined.problems[0].detail
    )
