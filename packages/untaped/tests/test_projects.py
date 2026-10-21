"""Projects: discovery, identity against the home's record, init, and plugins' committed files.

"Same folder" is the folder's filesystem identity, never its path string; a
command that only reads reports and never repairs, a writing use repairs
under the project's lock, and what can't be decided is refused (exit 4).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped import projects, refs
from untaped.errors import ConfigError, ErrorCategory, UsageError
from untaped.plugins.registry import PluginSpec
from untaped.projects import (
    Project,
    anchor_state,
    committed_path,
    current_project,
    find_root,
    holder_of,
    init,
    project_at,
    writing,
)
from untaped.refs import AnchorId, RefHolder, RefTarget
from untaped.testing import make_project, write_reference


def _reference(project: Project) -> refs.Reference:
    return refs.read_reference(refs.reference_file(holder_of(project)))


def _fresh(path: Path) -> Project | None:
    """``project_at`` as a new process sees it: nothing remembered from earlier calls."""
    projects._forget()
    return project_at(path)


def _lines(action: Callable[[], object], capsys: pytest.CaptureFixture[str]) -> list[str]:
    """The stderr lines ``action`` prints."""
    capsys.readouterr()
    action()
    return capsys.readouterr().err.splitlines()


# --- discovery -------------------------------------------------------------------------


def test_the_walk_stops_at_the_nearest_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    deep = project.root / "a" / "b"
    deep.mkdir(parents=True)
    assert find_root(deep) == project.root
    assert project_at(deep) == project


def test_a_untaped_folder_with_only_a_plugins_files_is_not_a_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    repo = project.root / "api"
    (repo / ".untaped").mkdir(parents=True)
    (repo / ".untaped" / "lint.yml").write_text("version: 1\n", encoding="utf-8")
    assert find_root(repo) == project.root  # the walk passes the committed lint file


def test_outside_a_project_there_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert project_at(tmp_path) is None
    with pytest.raises(UsageError, match=r"^not in a project: no \.untaped/project\.json in"):
        current_project()


def test_the_home_and_untaped_home_are_never_a_project() -> None:
    home = Path.home()
    for folder in (home, home / ".untaped"):
        (folder / ".untaped").mkdir(parents=True, exist_ok=True)
        (folder / ".untaped" / "project.json").write_text(
            '{"untaped": "1", "id": "3f9a1c2b7d10"}', encoding="utf-8"
        )
    inside = home / "notes"
    inside.mkdir()
    assert find_root(inside) is None
    assert find_root(home / ".untaped" / "workspaces") is None


def test_current_project_is_the_working_directorys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ")
    monkeypatch.chdir(project.root)
    assert current_project() == project
    assert current_project().name == "PROJ"


def test_a_damaged_marker_is_a_config_error(tmp_path: Path) -> None:
    folder = tmp_path / "PROJ" / ".untaped"
    folder.mkdir(parents=True)
    (folder / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="project marker"):
        project_at(tmp_path / "PROJ")


# --- init ------------------------------------------------------------------------------


def test_init_writes_the_marker_the_gitignore_and_the_reference(tmp_path: Path) -> None:
    done = init(tmp_path / "PROJ")
    assert done.action == "created"
    root = tmp_path / "PROJ"
    marker = json.loads((root / ".untaped" / "project.json").read_text(encoding="utf-8"))
    assert marker == {"untaped": "1", "id": done.project.id}
    assert (root / ".untaped" / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        ".gitignore",
        "project.json",
        "state.yml",
        "plugins/",
        "*.lock",
    ]
    ref = _reference(done.project)
    assert ref.anchor == root and ref.anchor_id == AnchorId.of(root) and ref.targets == ()
    assert ref.describe() == "project PROJ"


def test_init_adds_its_lines_to_a_gitignore_already_there(tmp_path: Path) -> None:
    folder = tmp_path / "PROJ" / ".untaped"
    folder.mkdir(parents=True)
    (folder / ".gitignore").write_text("state.yml\nnotes/", encoding="utf-8")
    (folder / "lint.yml").write_text("version: 1\n", encoding="utf-8")
    init(tmp_path / "PROJ")
    assert (folder / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "state.yml",
        "notes/",
        "project.json",
        "plugins/",
        "*.lock",
    ]
    assert (folder / "lint.yml").read_text(encoding="utf-8") == "version: 1\n"


def test_init_twice_changes_nothing(tmp_path: Path) -> None:
    first = init(tmp_path / "PROJ")
    again = init(tmp_path / "PROJ")
    assert (again.action, again.project.id) == ("unchanged", first.project.id)


def test_init_records_a_project_this_home_does_not_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path / "PROJ")
    refs.reference_file(holder_of(project)).unlink()  # a new machine, or a wiped home
    lines = _lines(lambda: _fresh(project.root), capsys)
    assert lines == [
        f"project PROJ ({project.root}) isn't recorded in this home; "
        "`untaped project init` there records it"
    ]
    assert not refs.reference_file(holder_of(project)).exists()  # reading wrote nothing
    assert init(project.root).action == "recorded"
    assert refs.reference_file(holder_of(project)).exists()


@pytest.mark.parametrize(
    ("where", "why"),
    [
        (lambda: Path.home(), "it is the home folder"),
        (lambda: Path.home() / ".untaped", "untaped's own folder"),
        (lambda: Path.home() / ".untaped" / "refs" / "x", "is untaped's own"),
        (lambda: Path.home() / ".untaped" / "plugins", "is untaped's own"),
    ],
    ids=["home", "untaped home", "refs", "plugins"],
)
def test_init_refuses_untapeds_own_folders(where: Callable[[], Path], why: str) -> None:
    with pytest.raises(UsageError, match=why):
        init(where())


def test_init_accepts_a_folder_under_the_default_workspaces_dir() -> None:
    done = init(Path.home() / ".untaped" / "workspaces" / "PROJ-1")
    assert done.action == "created"


def test_projects_do_not_nest(tmp_path: Path) -> None:
    outer = make_project(tmp_path / "work" / "PROJ")
    with pytest.raises(UsageError, match="is already a project; projects don't nest"):
        init(outer.root / "sub")
    with pytest.raises(UsageError, match=r"is a project inside .*; projects don't nest"):
        init(tmp_path / "work")


def test_new_id_is_only_for_a_copy(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    with pytest.raises(UsageError, match="records; --new-id is for a copy"):
        init(project.root, new_id=True)
    with pytest.raises(UsageError, match="not a project yet"):
        init(tmp_path / "other", new_id=True)


def test_a_second_init_racing_the_first_reports_one_project(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    done: list[projects.Initialized] = []
    real = projects._create_marker

    def create(marker: Path, pid: str) -> bool:
        barrier.wait()  # both have decided the folder is not a project yet
        return real(marker, pid)

    def run() -> None:
        done.append(init(tmp_path / "PROJ"))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(projects, "_create_marker", create)
        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    assert sorted(each.action for each in done) == ["created", "unchanged"]
    assert len({each.project.id for each in done}) == 1
    assert len([ref for ref in projects.project_references()]) == 1


def test_an_unwritable_home_keeps_the_marker_and_exits_4(tmp_path: Path) -> None:
    refs_dir = refs.refs_dir()
    refs_dir.parent.mkdir(parents=True, exist_ok=True)
    refs_dir.write_text("not a folder", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        init(tmp_path / "PROJ")
    assert caught.value.category is ErrorCategory.CONFIG
    assert (tmp_path / "PROJ" / ".untaped" / "project.json").is_file()


# --- identity --------------------------------------------------------------------------


def test_new_id_never_renews_a_folder_whose_record_it_cannot_read(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    file = refs.reference_file(holder_of(project))
    data = json.loads(file.read_text(encoding="utf-8"))
    file.write_text(json.dumps({**data, "untaped": "2"}), encoding="utf-8")  # a newer untaped's
    with pytest.raises(ConfigError, match="cannot read project PROJ's record"):
        init(project.root, new_id=True)
    assert _fresh(project.root) == project  # the id stays


def test_init_refuses_a_folder_holding_untapeds_home(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="untaped's own folder"):
        init(Path.home().parent)


def test_a_symlinked_spelling_is_the_same_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    link = tmp_path / "link"
    link.symlink_to(project.root)
    found = _fresh(link)
    assert found is not None and found.id == project.id and found.copy_of is None
    assert init(link).action == "unchanged"


def test_a_case_variant_spelling_is_the_same_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "Proj")
    variant = tmp_path / "proj"
    if not variant.exists():
        pytest.skip("this filesystem tells cases apart")
    assert init(variant).action == "unchanged"
    assert _reference(project).anchor == project.root


def test_a_moved_project_is_reported_then_repaired_by_a_writing_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path / "PROJ")
    moved = tmp_path / "archive" / "PROJ"
    moved.parent.mkdir()
    project.root.rename(moved)
    lines = _lines(lambda: _fresh(moved), capsys)
    assert lines == [
        f"project PROJ ({moved}) moved from {project.root}; "
        "`untaped project init` there repairs its record"
    ]
    assert _reference(project).anchor == project.root  # reading repaired nothing
    done = init(moved)
    assert (done.action, done.project.id) == ("repaired", project.id)
    assert _reference(project).anchor == moved


def test_a_copy_gets_its_own_id_only_on_a_writing_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path / "PROJ")
    copy = tmp_path / "PROJ-copy"
    shutil.copytree(project.root, copy)
    lines = _lines(lambda: _fresh(copy), capsys)
    assert lines == [
        f"{copy} is a copy of project PROJ ({project.root}); "
        "`untaped project init` there gives it its own id"
    ]
    seen = project_at(copy)
    assert seen is not None and seen.id == project.id and seen.copy_of == project.root
    assert [ref.anchor for ref in projects.project_references()] == [project.root]
    renewed, _ = writing(seen)
    assert renewed.id != project.id and renewed.copy_of is None
    assert json.loads((copy / ".untaped" / "project.json").read_text())["id"] == renewed.id
    assert _reference(renewed).anchor == copy and _reference(renewed).targets == ()
    assert _reference(project).anchor == project.root  # the original keeps its id


def test_a_copy_whose_original_is_gone_is_undecided(tmp_path: Path) -> None:
    # As a move across filesystems: the identity doesn't match, the original isn't there.
    project = make_project(tmp_path / "PROJ")
    copy = tmp_path / "elsewhere"
    shutil.copytree(project.root, copy)
    shutil.rmtree(project.root)
    seen = _fresh(copy)
    assert seen is not None
    with pytest.raises(ConfigError, match="cannot tell whether project elsewhere moved or was"):
        writing(seen)
    done = init(copy, new_id=True)
    assert done.action == "new id" and done.project.id != project.id


def test_two_folders_with_one_id_neither_recorded_are_undecided(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    copy = tmp_path / "copy"
    shutil.copytree(project.root, copy)
    project.root.rename(tmp_path / "kept")  # the recorded folder lives on elsewhere
    shutil.copytree(copy, project.root)  # and a copy sits at its path: another folder
    seen = _fresh(copy)
    assert seen is not None
    with pytest.raises(ConfigError, match="holds its id too, and neither is the folder"):
        writing(seen)


def test_an_unreadable_record_stops_a_writing_use(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    refs.reference_file(holder_of(project)).write_text("{", encoding="utf-8")
    seen = _fresh(project.root)
    assert seen is not None
    with pytest.raises(ConfigError, match="cannot read project PROJ's record"):
        writing(seen)


def test_a_changed_identity_at_the_same_path_is_refreshed(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    write_reference(holder_of(project), [], anchor=project.root, anchor_id=AnchorId(dev=0, ino=1))
    seen = _fresh(project.root)
    assert seen is not None and seen.copy_of is None
    writing(seen)
    assert _reference(project).anchor_id == AnchorId.of(project.root)


def test_a_writing_use_keeps_the_holds_a_move_carries(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    held = RefTarget(plugin="workspace", store="repos", item="github.com/acme/api")
    write_reference(
        holder_of(project), [held], anchor=project.root, anchor_id=AnchorId.of(project.root)
    )
    moved = tmp_path / "moved"
    project.root.rename(moved)
    seen = _fresh(moved)
    assert seen is not None
    writing(seen)
    assert _reference(project).targets == (held,)


# --- states ----------------------------------------------------------------------------


def _another_device(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    """Make ``folder`` read as being on another device, as an unmounted drive's parent does."""
    real = Path.stat

    def stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        found = real(self, follow_symlinks=follow_symlinks)
        if self == folder:
            fields = list(found)
            fields[2] = found.st_dev + 1
            return os.stat_result(fields)
        return found

    monkeypatch.setattr(Path, "stat", stat)


def test_states_present_missing_and_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "drive" / "PROJ")
    assert anchor_state(_reference(project)) == "present"
    shutil.rmtree(project.root)
    assert anchor_state(_reference(project)) == "missing"
    _another_device(monkeypatch, tmp_path / "drive")  # the drive is no longer mounted
    assert anchor_state(_reference(project)) == "unreadable"


def test_another_id_at_the_folder_reads_missing(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    (project.root / ".untaped" / "project.json").write_text(
        '{"untaped": "1", "id": "000000000000"}', encoding="utf-8"
    )
    assert anchor_state(_reference(project)) == "missing"


def test_a_copy_while_the_original_drive_is_away_is_undecided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "drive" / "PROJ")
    copy = tmp_path / "copy"
    shutil.copytree(project.root, copy)
    shutil.rmtree(project.root)
    _another_device(monkeypatch, tmp_path / "drive")
    seen = _fresh(copy)
    assert seen is not None
    with pytest.raises(ConfigError, match=r"\(unreadable\), and this folder isn't that one"):
        writing(seen)


def test_an_unlistable_refs_folder_is_kept_as_unknown() -> None:
    folder = refs.refs_dir()
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("not a folder", encoding="utf-8")  # listing it fails
    [ref] = projects.project_references()
    assert isinstance(projects.view(ref), projects.UnknownHolder)


def test_only_project_references_are_projects(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    write_reference(RefHolder(kind="acme_job", name="build-1"), [], anchor=tmp_path)
    (refs.refs_dir() / "project-ffffffffffff.json").write_text("{", encoding="utf-8")
    found = [projects.view(ref) for ref in projects.project_references()]
    assert [type(each).__name__ for each in found] == ["HolderView", "UnknownHolder"]
    view = found[0]
    assert isinstance(view, projects.HolderView)
    assert (view.holder, view.label, view.anchor, view.state) == (
        holder_of(project),
        "project PROJ",
        project.root,
        "present",
    )


# --- locks -----------------------------------------------------------------------------


def test_the_project_lock_is_re_entrant_and_shuts_out_other_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ")
    monkeypatch.setattr(projects, "LOCK_WAIT", 0.0)
    busy: list[BaseException] = []

    def other() -> None:
        try:
            with project.locked():
                pass
        except ConfigError as exc:
            busy.append(exc)

    with project.locked(), project.locked():
        worker = threading.Thread(target=other)
        worker.start()
        worker.join()
    [error] = busy
    assert str(error) == "another untaped is changing project PROJ; try again"
    assert error.category is ErrorCategory.UNAVAILABLE


# --- committed files -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plugin", "name"),
    [("lint", "lint.yml"), ("lint", "lint"), ("lint", "lint/house.py"), ("awx", "awx")],
)
def test_committed_path_accepts_a_plugins_own_names(tmp_path: Path, plugin: str, name: str) -> None:
    spec = PluginSpec(name=plugin)
    assert committed_path(spec, name, root=tmp_path) == tmp_path / ".untaped" / name
    assert committed_path(spec, name, root=Path()) == Path(".untaped") / name


@pytest.mark.parametrize(
    "name",
    [
        "awx",
        "awx/tests",
        "project.json",
        "config.yml",
        "state.yml",
        "plugins",
        ".gitignore",
        "lint.",
        "lint/",
        "lint/../config.yml",
        "lint.d/x.yml",
        "lintx.yml",
        "lint\\x",
    ],
)
def test_committed_path_refuses_another_plugins_and_cores_names(name: str) -> None:
    with pytest.raises(ValueError, match=r"is not plugin lint's to name under \.untaped/"):
        committed_path(PluginSpec(name="lint"), name, root=Path())


def test_plugin_dir_is_under_the_projects_untaped_folder(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    assert project.plugin_dir(PluginSpec(name="acme")) == (
        project.root / ".untaped" / "plugins" / "acme"
    )
