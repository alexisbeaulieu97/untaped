"""A ``Dir``'s layouts and items: named by references, pruned, held, listed by doctor."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, RootModel

from untaped.core.commands.doctor import collect_doctor_rows
from untaped.runtime import bootstrap
from untaped.sdk.plugin import Plugin
from untaped.sdk.projects import holder_of
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import holds
from untaped.sdk.storage._refs import RefTarget
from untaped.sdk.storage.dir import Dir
from untaped.sdk.storage.holds import LetGo, Refused, ReleaseAsk
from untaped.sdk.testing import Install, install, make_project, plan_release


class Settings(BaseModel):
    """Nothing to set."""


def let_go(ask: ReleaseAsk[str]) -> LetGo[str] | Refused:
    keep = "keeps the repo" if ask.others else "deletes the repo"
    return LetGo(preview=keep, apply=lambda _ask: keep)


class Atlas(Plugin):
    """A plugin keeping numbered maps, and repos its holders hold."""

    settings = Settings
    maps = Dir(layout=3)
    index = Dir(layout=2, file="index.sqlite3")
    notes = Dir()
    repos = Dir(items=str, release=let_go, moved=None)


@pytest.fixture
def atlas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Install:
    env = tmp_path / "venv"
    (env / "untaped-11.1.0.dist-info").mkdir(parents=True)
    monkeypatch.setattr(refs, "environment", lambda: env)
    monkeypatch.setattr(refs, "_version", lambda: "11.1.0")
    return install(Atlas)


def test_references_name_a_numbered_dir_from_the_declaration(atlas: Install) -> None:
    with atlas.running():
        assert RefTarget(plugin="atlas", store="maps", layout=3) in (refs.composed_targets() or ())
        names = {target.store for target in refs.composed_targets() or ()}
    assert "notes" not in names  # an unnumbered place has no layouts


def test_a_registered_install_prunes_the_layouts_nothing_holds(atlas: Install) -> None:
    with atlas.running():
        old = Atlas.maps.place / "maps.v2"
        old.mkdir(parents=True)
        (old / "map.json").write_text("{}")
        refs.register(refs.composed_targets() or ())
        Atlas.maps.open()
        Atlas.maps.prune()
        assert not old.exists()
        assert Atlas.maps.path.is_dir()


def test_a_numbered_file_prunes_its_other_layouts(atlas: Install) -> None:
    with atlas.running():
        Atlas.index.place.mkdir(parents=True)
        old = Atlas.index.place / "index.v1.sqlite3"
        old.write_text("old")
        refs.register(refs.composed_targets() or ())
        Atlas.index.open()
        Atlas.index.prune()
        assert not old.exists()


def test_an_unregistered_install_keeps_them(atlas: Install) -> None:
    with atlas.running():
        old = Atlas.maps.place / "maps.v2"
        old.mkdir(parents=True)
        Atlas.maps.open()
        Atlas.maps.prune()
        assert old.is_dir()


def test_an_unnumbered_place_has_no_layouts_to_prune(atlas: Install) -> None:
    with atlas.running(), pytest.raises(Exception, match="is not numbered"):
        Atlas.notes.prune()


def test_doctor_lists_a_numbered_dirs_layouts(atlas: Install) -> None:
    with atlas.running():
        old = Atlas.maps.place / "maps.v2"
        old.mkdir(parents=True)
        Atlas.maps.open()
        rows = collect_doctor_rows(bootstrap.SHELL_SPEC, bootstrap.composition())
    [row] = [row for row in rows if row["check"] == "stores"]
    assert row["status"] == "warn"
    assert "atlas.maps: layout 3 at" in str(row["detail"])
    assert "layout 2 at" in str(row["detail"]) and "held by nobody" in str(row["detail"])


def test_a_dir_with_items_is_an_item_store_holders_hold(atlas: Install, tmp_path: Path) -> None:
    with atlas.running():
        repos = Atlas.repos.items()
        project = make_project(tmp_path / "PROJ", holds={repos: ["github.com/acme/api"]})
        [plan] = plan_release(project, repos)
        assert isinstance(plan, LetGo) and plan.preview == "deletes the repo"
        assert [h.holder.name for h in repos.holders("github.com/acme/api")] == [project.id]
        assert all(t.store != "repos" for t in refs.composed_targets() or ())  # never a layout
        holds.add_hold(holder_of(project), repos, "github.com/acme/web")  # composition lists it
        assert {target.item for target in refs.read_references()[0].targets} == {
            "github.com/acme/api",
            "github.com/acme/web",
        }


def test_items_need_a_release_and_a_say_on_moves() -> None:
    with pytest.raises(TypeError, match="items= needs release="):
        Dir(items=str, release=let_go)
    with pytest.raises(TypeError, match="go with items="):
        Dir(moved=None)
    with pytest.raises(TypeError, match="layout= and items= don't go together"):
        Dir(layout=2, items=str, release=let_go, moved=None)
    with pytest.raises(TypeError, match="items takes str or a RootModel"):
        Dir(items=int, release=let_go, moved=None)  # type: ignore[arg-type]


def test_a_place_without_items_holds_nothing(atlas: Install) -> None:
    with atlas.running(), pytest.raises(Exception, match="declares no items="):
        Atlas.maps.items()


class RepoKey(RootModel[str]):
    """A repo, as its owner names it."""


def test_items_can_be_a_root_model(atlas: Install) -> None:
    keys = Dir(items=RepoKey, release=let_go, moved=None)._spelled_as("atlas", "keys")
    with atlas.running():
        assert keys.items().item is RepoKey
