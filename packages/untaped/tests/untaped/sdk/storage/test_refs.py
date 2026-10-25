"""References: the install identity, one file per holder, liveness, and the per-start refresh."""

from __future__ import annotations

import contextvars
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped.sdk.errors import ConfigError
from untaped.sdk.run import Run
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import data_file
from untaped.sdk.storage._refs import RefHolder, RefTarget
from untaped.sdk.storage.data_file import written_by
from untaped.sdk.storage.store import Store
from untaped.sdk.testing import write_reference

pytestmark = pytest.mark.usefixtures("home_run")


def _install(root: Path, version: str = "11.1.0") -> Path:
    """A directory that holds an untaped distribution, as any installer leaves one."""
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This process's environment: a fake site-packages holding untaped 11.1.0."""
    env = _install(tmp_path / "venv" / "site-packages")

    class _Dist:
        version = "11.1.0"

        @staticmethod
        def locate_file(_name: str) -> Path:
            return env

    monkeypatch.setattr(refs, "distribution", lambda _name: _Dist())
    return env


# --- identity ------------------------------------------------------------------


def test_the_holder_comes_from_the_distribution_directory_alone(site: Path) -> None:
    assert refs.environment() == site
    digest = hashlib.sha256(str(site).encode()).hexdigest()[:12]
    assert refs.install_holder(site) == RefHolder(kind="install", name=digest)


def test_a_source_tree_has_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> object:
        raise PackageNotFoundError("untaped")

    monkeypatch.setattr(refs, "distribution", missing)
    assert refs.environment() is None
    with pytest.raises(ConfigError, match="source tree"):
        refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    assert refs.unregister() is None
    assert refs.this_install() is None


def test_the_real_environment_is_the_directory_holding_untaped() -> None:
    env = refs.environment()
    assert env is not None
    assert any(env.glob("untaped-*.dist-info"))


# --- the file ------------------------------------------------------------------


def test_the_references_are_in_cores_refs_block(home_run: Run) -> None:
    from untaped.core.plugin import Core

    assert Core.refs is refs.REFS
    assert refs.REFS.place == home_run.home / "plugins" / "untaped" / "refs"


def test_register_writes_one_file_for_this_install(site: Path) -> None:
    ref = refs.register(
        [
            RefTarget(plugin="acme", store="data", layout=2),
            RefTarget(plugin="ansible", store="index", layout=4),
        ],
        plugins=["acme", "ansible"],
    )

    holder = refs.install_holder(site)
    assert ref.file == refs.REFS.place / f"install-{holder.name}.json"
    data = json.loads(ref.file.read_text())
    assert data == {
        "untaped": {"file": 1, "by": written_by()},
        "holder": {"kind": "install", "name": holder.name},
        "anchor": str(site),
        "version": "11.1.0",
        "plugins": ["acme", "ansible"],
        "targets": [
            {"plugin": "acme", "store": "data", "layout": 2},
            {"plugin": "ansible", "store": "index", "layout": 4},
        ],
    }
    assert refs.this_install() == ref
    assert ref.live and not ref.pre_upgrade
    assert ref.layouts(Store("acme", "data", base=lambda: Path("x"), layout=2)) == {2}


def test_unregister_removes_the_file(site: Path) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    removed = refs.unregister()
    assert removed is not None and not removed.file.exists()
    assert refs.unregister() is None
    assert refs.this_install() is None


def test_a_holders_file_is_its_kind_and_name() -> None:
    holder = RefHolder(kind="workspace", name="acme.pay-1")
    file = refs.reference_file(holder)
    assert file == refs.REFS.place / "workspace-acme.pay-1.json"
    write_reference(
        holder,
        [RefTarget(plugin="git", store="store", item="github.com/acme/pay")],
        plugin="workspace",
    )
    [ref] = refs.read_references()
    assert ref.file == file and ref.holder == holder
    assert ref.kind == "workspace" and ref.plugin == "workspace"


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("work-space", "pay"),  # a "-" in the kind would make two holders share a file
        ("workspace", "Pay"),  # upper case would, on a case-insensitive disk
        ("workspace", "acme/pay"),
        ("workspace", ".pay"),
        ("workspace", "p" * 129),
        ("", "pay"),
    ],
)
def test_a_holder_that_could_share_or_escape_a_file_is_refused(kind: str, name: str) -> None:
    with pytest.raises(ValidationError):
        RefHolder(kind=kind, name=name)


def test_a_reference_is_live_while_its_directory_holds_untaped(tmp_path: Path) -> None:
    env = _install(tmp_path / "other", "11.0.0")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=1)],
        anchor=env,
        version="11.0.0",
    )
    [ref] = refs.read_references()
    assert ref.live and not ref.pre_upgrade

    (env / "untaped-11.0.0.dist-info").rename(env / "untaped-11.2.0.dist-info")
    assert ref.live and ref.pre_upgrade  # upgraded in place, not run since

    (env / "untaped-11.2.0.dist-info").rmdir()
    assert not ref.live  # uninstalled: the directory is still there, untaped is not
    env.rmdir()
    assert not ref.live


def test_an_anchorless_holder_lives_until_released() -> None:
    write_reference(
        RefHolder(kind="workspace", name="pay"),
        [RefTarget(plugin="git", store="store", item="github.com/acme/pay")],
        plugin="workspace",
    )
    [ref] = refs.read_references()
    assert ref.live
    assert refs.holders(RefTarget(plugin="git", store="store", item="github.com/acme/pay")) == [
        RefHolder(kind="workspace", name="pay")
    ]


def test_holders_lists_only_live_references(tmp_path: Path) -> None:
    alive = _install(tmp_path / "alive")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=1)],
        anchor=alive,
        version="11.1.0",
    )
    write_reference(
        RefHolder(kind="install", name="bbbbbbbbbbbb"),
        [RefTarget(plugin="acme", store="data", layout=1)],
        anchor=tmp_path / "gone",
    )
    assert refs.holders(RefTarget(plugin="acme", store="data", layout=1)) == [
        RefHolder(kind="install", name="aaaaaaaaaaaa")
    ]
    assert refs.holders(RefTarget(plugin="acme", store="data", layout=2)) == []


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (
            '{"untaped": {"file": 2}, "holder": {"kind": "install", "name": "x"}, "targets": []}',
            "newer untaped",
        ),
        ('{"untaped": "x"}', "unreadable"),
        ("not json", "unreadable"),
        ("[]", "unreadable"),
        ('{"untaped": {"file": 1}, "holder": "install:x", "targets": []}', "holder is malformed"),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x", "extra": 1}, '
            '"targets": []}',
            "holder is malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": ""}, "targets": []}',
            "malformed",
        ),
        ('{"untaped": {"file": 1}, "targets": []}', "unreadable"),
        ('{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}}', "no targets"),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, '
            '"targets": ["acme.data@1"]}',
            "malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, "targets": '
            '[{"plugin": "acme", "store": "data", "layout": 1, "item": "a"}]}',
            "malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, "targets": '
            '[{"plugin": "acme", "store": "data", "layout": 1, "extra": 1}]}',
            "malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, "targets": '
            '[{"plugin": "", "store": "data", "layout": 1}]}',
            "malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, "targets": '
            '[{"plugin": "acme", "store": "data"}]}',
            "malformed",
        ),
        (
            '{"untaped": {"file": 1}, "holder": {"kind": "install", "name": "x"}, "targets": '
            '[{"plugin": "acme", "store": "data", "layout": "1"}]}',
            "malformed",
        ),
    ],
)
def test_an_unreadable_or_newer_reference_is_opaque(content: str, why: str) -> None:
    folder = refs.REFS.place
    folder.mkdir(parents=True)
    (folder / "install-x.json").write_text(content)
    [ref] = refs.read_references()
    assert ref.opaque is not None and why in ref.opaque
    assert ref.live  # it pauses pruning
    with pytest.raises(ConfigError, match=r"cannot tell who holds acme\.data layout 1"):
        refs.holders(RefTarget(plugin="acme", store="data", layout=1))  # unknown, never "nobody"


def test_a_reference_of_an_unknown_kind_is_live_and_holds_what_it_can_read(
    tmp_path: Path,
) -> None:
    folder = refs.REFS.place
    folder.mkdir(parents=True)
    target = {"plugin": "git", "store": "store", "item": "github.com/acme/pay"}
    (folder / "acme_job-pay.json").write_text(
        json.dumps(
            {
                "untaped": {"file": 1},
                "holder": {"kind": "acme_job", "name": "pay"},
                "anchor": str(tmp_path / "gone"),  # no untaped there: an install would be dead
                "targets": [
                    target,
                    "git.store#old",
                    {"plugin": "git"},
                    {"plugin": "acme", "store": "data", "layout": 4, "new": 1},
                ],
            }
        )
    )
    (folder / "acme_job-bare.json").write_text(
        '{"untaped": {"file": 1}, "holder": {"kind": "acme_job", "name": "bare"}}'
    )
    bare, pay = refs.read_references()
    assert pay.opaque is None and pay.live and not pay.pre_upgrade
    assert pay.kind == "acme_job"
    assert pay.targets == (RefTarget.model_validate(target),)
    assert bare.opaque is None and bare.live and bare.targets == ()
    assert refs.holders(RefTarget.model_validate(target)) == [
        RefHolder(kind="acme_job", name="pay")
    ]


def test_the_plugins_list_is_written_even_empty_and_a_file_without_it_does_not_say(
    tmp_path: Path,
) -> None:
    holder = RefHolder(kind="install", name="aaaaaaaaaaaa")
    file = write_reference(holder, [], anchor=tmp_path, plugins=[])
    assert json.loads(file.read_text())["plugins"] == []
    assert refs.read_reference(file).plugins == ()
    write_reference(holder, [], anchor=tmp_path)  # left out, as a project's file
    assert "plugins" not in json.loads(file.read_text())
    assert refs.read_reference(file).plugins is None  # absent: it doesn't say, unlike empty
    data = json.loads(file.read_text())
    file.write_text(json.dumps({**data, "plugins": "jira"}))
    assert refs.read_reference(file).opaque == "unreadable (plugins is malformed)"


def test_only_an_install_is_ever_from_before_an_upgrade(tmp_path: Path) -> None:
    anchor = _install(tmp_path / "job", "9.0.0")
    write_reference(RefHolder(kind="acme_job", name="x"), [], anchor=anchor, version="1.0.0")
    [ref] = refs.read_references()
    assert ref.live and not ref.pre_upgrade


def test_an_item_target_is_written_without_a_layout() -> None:
    target = RefTarget(plugin="git", store="store", item="github.com/acme/pay")
    file = write_reference(RefHolder(kind="acme_job", name="pay"), [target])
    assert json.loads(file.read_text())["targets"] == [
        {"plugin": "git", "store": "store", "item": "github.com/acme/pay"}
    ]
    [ref] = refs.read_references()
    assert ref.targets == (target,)


def _project_file(tmp_path: Path, **fields: object) -> Path:
    data: dict[str, object] = {
        "untaped": {"file": 1},
        "holder": {"kind": "project", "name": "3f9a1c2b7d10"},
        "anchor": str(tmp_path / "PROJ-123"),
        "anchor_id": {"dev": 1, "ino": 2},
        "targets": [],
    }
    data.update(fields)
    folder = refs.REFS.place
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "project-3f9a1c2b7d10.json"
    file.write_text(json.dumps(data))
    return file


def test_a_project_reference_reads_its_anchor_and_identity(tmp_path: Path) -> None:
    _project_file(tmp_path)
    [ref] = refs.read_references()
    assert ref.opaque is None
    assert ref.anchor == tmp_path / "PROJ-123"
    assert ref.anchor_id == refs.AnchorId(dev=1, ino=2)
    assert ref.describe() == "project PROJ-123"
    assert ref.moved_from == ()


def test_a_project_reference_reads_each_folder_a_repair_is_pending_from(tmp_path: Path) -> None:
    _project_file(tmp_path, moved_from=[str(tmp_path / "A"), str(tmp_path / "B")])
    [ref] = refs.read_references()
    assert ref.moved_from == (tmp_path / "A", tmp_path / "B")


def test_a_project_reference_is_live_whatever_its_folder_holds(tmp_path: Path) -> None:
    # The folder is gone: an install there would be dead, a project never is here.
    _project_file(tmp_path)
    [ref] = refs.read_references()
    assert ref.live and not ref.pre_upgrade


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"targets": [{"plugin": "workspace"}]}, "a target is malformed"),
        ({"targets": "workspace.repos"}, "no targets"),
        ({"anchor": ""}, "no anchor"),
        ({"anchor_id": {"dev": 1}}, "no anchor identity"),
        ({"anchor_id": None}, "no anchor identity"),
        ({"moved_from": "/x"}, "moved_from is malformed"),
        ({"moved_from": [""]}, "moved_from is malformed"),
        ({"moved_from": [1]}, "moved_from is malformed"),
    ],
)
def test_a_project_reference_is_read_strictly(
    tmp_path: Path, fields: dict[str, object], why: str
) -> None:
    # A project is this untaped's own kind: it never silently holds less than it says.
    _project_file(tmp_path, **fields)
    [ref] = refs.read_references()
    assert ref.opaque == f"unreadable ({why})"
    assert ref.live


def test_write_reference_records_the_anchor_identity(tmp_path: Path) -> None:
    holder = RefHolder(kind="project", name="3f9a1c2b7d10")
    anchor_id = refs.AnchorId(dev=5, ino=6)
    file = write_reference(holder, [], anchor=tmp_path, anchor_id=anchor_id)
    assert json.loads(file.read_text())["anchor_id"] == {"dev": 5, "ino": 6}
    assert refs.read_reference(file).anchor_id == anchor_id


def test_a_missing_folder_has_no_references() -> None:
    assert refs.read_references() == []


# --- the refresh ---------------------------------------------------------------


@pytest.fixture
def composed_names(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The plugins this run's composition has; a test edits it."""
    names: list[str] = []
    monkeypatch.setattr(refs, "composed_plugins", lambda: tuple(names))
    return names


@pytest.fixture
def composed(monkeypatch: pytest.MonkeyPatch, composed_names: list[str]) -> list[RefTarget]:
    """What this run's composition declares; a test edits it."""
    targets = [RefTarget(plugin="acme", store="data", layout=1)]
    monkeypatch.setattr(refs, "composed_targets", lambda: tuple(targets))
    return targets


def test_a_refresh_follows_the_plugins_the_install_composes(
    site: Path, composed: list[RefTarget], composed_names: list[str]
) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)], plugins=["acme"])
    composed_names[:] = ["acme", "jira"]  # jira installed beside acme

    refs.refresh()

    assert json.loads(ref.file.read_text())["plugins"] == ["acme", "jira"]
    assert refs.this_install() is not None and refs.this_install().plugins == ("acme", "jira")


def test_a_store_open_leaves_the_reference_to_the_start(
    site: Path, composed: list[RefTarget], tmp_path: Path
) -> None:
    """A store opened under a narrower composition (a plugin check's) records nothing."""
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    composed[:] = [RefTarget(plugin="acme", store="data", layout=2)]

    Store("acme", "data", base=lambda: tmp_path / "data.db", layout=2).open()

    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 1}
    ]


def test_every_refresh_compares_again(site: Path, composed: list[RefTarget]) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    refs.refresh()
    composed[:] = [RefTarget(plugin="acme", store="data", layout=9)]
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 9}
    ]


def test_an_unchanged_reference_is_not_rewritten(
    site: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    writes: list[object] = []
    monkeypatch.setattr(data_file, "atomic_write", lambda *args, **_kw: writes.append(args))
    monkeypatch.setattr(refs.REFS, "try_locked", _unlocked)
    refs.refresh()
    assert writes == []


def _unlocked(_name: str | None = None) -> object:
    raise AssertionError("an unchanged reference took the references' lock")


@contextmanager
def _busy(_name: str | None = None) -> Iterator[bool]:
    yield False  # another process holds the references' lock


def test_a_busy_lock_leaves_the_refresh_to_the_next_start(
    site: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    composed[:] = [RefTarget(plugin="acme", store="data", layout=2)]
    with monkeypatch.context() as busy:
        busy.setattr(refs.REFS, "try_locked", _busy)
        refs.refresh()  # never waits
    assert json.loads(ref.file.read_text())["targets"][0]["layout"] == 1
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"][0]["layout"] == 2


def test_a_refresh_outside_a_run_never_raises(site: Path, composed: list[RefTarget]) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    composed[:] = [RefTarget(plugin="acme", store="data", layout=2)]
    contextvars.Context().run(refs.refresh)  # a library call: no run, so nothing to place
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 1}
    ]
    refs.refresh()  # the run's own start still refreshes
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 2}
    ]


def test_no_run_registers(site: Path, composed: list[RefTarget], tmp_path: Path) -> None:
    Store("acme", "data", base=lambda: tmp_path / "data.db").open()
    assert refs.read_references() == []


def test_a_refresh_without_a_composition_keeps_the_file(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    monkeypatch.setattr(refs, "composed_targets", lambda: None)
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 1}
    ]


def test_a_refresh_before_the_composition_exists_leaves_the_next_one_to_refresh(
    site: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=1)])
    composed[:] = [RefTarget(plugin="acme", store="data", layout=2)]
    with monkeypatch.context() as early:
        early.setattr(refs, "composed_targets", lambda: None)  # nothing composed yet
        refs.refresh()
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 2}
    ]


def test_a_failed_write_gives_no_error_and_the_next_start_retries(
    site: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = refs.register([RefTarget(plugin="acme", store="data", layout=0)])

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only")

    with monkeypatch.context() as read_only:
        read_only.setattr(data_file, "atomic_write", refused)
        refs.refresh()  # never raises: the next start tries again
    refs.refresh()
    assert json.loads(ref.file.read_text())["targets"] == [
        {"plugin": "acme", "store": "data", "layout": 1}
    ]


def test_the_composition_names_every_declared_store() -> None:
    from untaped.runtime.bootstrap import compose_root, kept_composition
    from untaped.runtime.registry import PluginSpec
    from untaped.sdk.testing import plugin_candidate

    data = Store("acme", "data", base=lambda: Path("/x/data.db"), layout=3)
    other = Store("acme", "catalogue", base=lambda: Path("/x/catalogue"), layout=1)
    spec = PluginSpec(name="acme", stores=(data, other))
    with kept_composition(quiet=True):
        compose_root(candidates=[plugin_candidate(spec)])
        assert refs.composed_targets() == (
            RefTarget(plugin="acme", store="catalogue", layout=1),
            RefTarget(plugin="acme", store="data", layout=3),
        )
