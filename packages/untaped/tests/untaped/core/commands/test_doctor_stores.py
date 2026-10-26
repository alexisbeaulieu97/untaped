"""The ``stores`` and ``references`` doctor rows: each layout on disk, and who holds it."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.core.commands.doctor import collect_doctor_rows
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.runtime.run import build_run
from untaped.sdk._run import run_scope
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage._refs import RefHolder, RefTarget
from untaped.sdk.storage.store import Store
from untaped.sdk.testing import make_project, plugin_candidate, write_reference

pytestmark = pytest.mark.usefixtures("home_run")


def _install(root: Path, version: str = "11.1.0") -> Path:
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


def _row(*stores: Store, check: str = "stores") -> dict[str, object]:
    spec = PluginSpec(name="acme", stores=stores)
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec)])
    # under the composition's run, as doctor runs, so a fix is spelled from its class
    with run_scope(build_run(result.mount.declaration, result.mount.settings)):
        rows = collect_doctor_rows(bootstrap.SHELL_SPEC, result)
    [row] = [row for row in rows if row["check"] == check]
    return row


def _store(base: Path, *, layout: int = 4) -> Store:
    return Store("acme", "data", base=lambda: base, layout=layout)


def test_a_store_not_created_yet_passes(tmp_path: Path) -> None:
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert row["detail"] == f"acme.data: layout 4 at {tmp_path / 'data.v4.db'}, not created yet"


def test_one_layout_on_disk_passes_with_its_size(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x" * 2048)
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert row["detail"] == f"acme.data: layout 4 at {tmp_path / 'data.v4.db'} (2.0 KiB)"


def test_another_layout_nobody_holds_on_an_unregistered_machine_warns(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x")
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert row["detail"] == (
        f"acme.data: layout 4 at {tmp_path / 'data.v4.db'} (1 B); "
        f"layout 3 at {tmp_path / 'data.v3.db'} (3 B), held by nobody: "
        "no registered install keeps this store, so nothing cleans up"
    )
    assert row["fix"] == ["--profile", "default", "setup", "register"]
    assert row["automatic"] is False  # a lasting registration is a deliberate step


def test_two_held_layouts_pass(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x")
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    checkout = _install(tmp_path / "checkout")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=3)],
        anchor=checkout,
        version="11.1.0",
    )
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert "(1 B), held by this install;" in str(row["detail"])
    assert f"(3 B), held by untaped 11.1.0 at {checkout}" in str(row["detail"])


def test_an_unheld_layout_the_next_fill_removes_passes(tmp_path: Path) -> None:
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert "held by nobody (removed at the next complete fill)" in str(row["detail"])


def test_a_layout_a_running_process_has_open_warns(tmp_path: Path) -> None:
    old = tmp_path / "data.v3.db"
    old.write_bytes(b"xyz")
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    reader = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=3)
    reader.open()  # this process reads layout 3 as another install would
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert "held by nobody: a running untaped has it open" in str(row["detail"])
    assert row["fix"] is None


def test_a_layout_whose_lock_the_filesystem_refuses_says_to_delete_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.sdk.storage import store as store_module

    old = tmp_path / "data.v3.db"
    old.write_bytes(b"xyz")
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    monkeypatch.setattr(store_module, "_probe", lambda _path: "refused")
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert "refuses its in-use lock" in str(row["detail"])
    assert "delete it and its in-use lock by hand while no untaped runs" in str(row["detail"])
    assert row["fix"] is None


def test_a_damaged_lock_is_named_with_what_to_delete(tmp_path: Path) -> None:
    own = tmp_path / "data.v4.db"
    own.write_bytes(b"x")
    Path(f"{own}.inuse").write_bytes(b"garbage, not a database")
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert f"its in-use lock {own}.inuse is damaged" in str(row["detail"])
    assert "delete that file while no untaped runs" in str(row["detail"])
    assert row["fix"] is None


def test_a_reference_this_untaped_cannot_read_warns(tmp_path: Path) -> None:
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    (refs.REFS.place / "install-future.json").write_text('{"untaped": {"file": 2}}')
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert "a reference this untaped cannot read pauses cleanup" in str(row["detail"])


def test_a_store_whose_base_fails_is_named(tmp_path: Path) -> None:
    def broken() -> Path:
        raise RuntimeError("no home")

    row = _row(Store("acme", "data", base=broken))
    assert row["status"] == "warn"
    assert row["detail"] == "acme.data: could not find its files: no home"


def test_no_store_passes() -> None:
    result = bootstrap.compose_root(candidates=[])
    [row] = [
        row for row in collect_doctor_rows(bootstrap.SHELL_SPEC, result) if row["check"] == "stores"
    ]
    assert (row["status"], row["detail"]) == ("pass", "no plugin declares a store")


# --- references ----------------------------------------------------------------


def test_an_unregistered_install_with_one_layout_passes_with_a_note(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x")
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert (row["status"], row["detail"]) == ("pass", "this install: not registered")


def test_an_unregistered_install_with_another_layout_warns(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x")
    (tmp_path / "data.v3.db").write_bytes(b"x")
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert row["status"] == "warn"
    assert str(row["detail"]).startswith("this install is not registered: nothing cleans up")
    assert row["fix"] == ["--profile", "default", "setup", "register"]


def test_every_holder_is_listed(tmp_path: Path) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    gone = tmp_path / "gone"
    old = _install(tmp_path / "old", "11.2.0")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=3)],
        anchor=gone,
        version="11.0.0",
    )
    write_reference(
        RefHolder(kind="install", name="bbbbbbbbbbbb"),
        [RefTarget(plugin="acme", store="data", layout=3)],
        anchor=old,
        version="11.1.0",
    )
    write_reference(
        RefHolder(kind="workspace", name="pay"),
        [RefTarget(plugin="git", store="store", item="github.com/acme/pay")],
        plugin="workspace",
    )
    job = _install(tmp_path / "job", "9.0.0")  # a later untaped's kind, no plugin
    write_reference(RefHolder(kind="acme_job", name="pay"), [], anchor=job, version="1.0.0")
    row = _row(_store(tmp_path / "data.db"), check="references")
    detail = str(row["detail"])
    assert row["status"] == "warn"  # a dead reference is a person's to remove
    assert row["fix"] == ["--profile", "default", "setup", "prune"]
    assert f"untaped 11.0.0 at {gone}: not reachable" in detail
    assert f"untaped 11.1.0 at {old}: reference from before an upgrade" in detail
    assert "workspace pay (plugin workspace)" in detail
    assert "acme_job pay" in detail and "acme_job pay (" not in detail
    assert "acme_job pay:" not in detail  # neither dead nor from before an upgrade
    assert "(this install)" in detail


def test_projects_are_listed_with_their_state(tmp_path: Path) -> None:
    refs.register([])
    present = tmp_path / "PROJ-1"
    make_project(present)
    gone = tmp_path / "PROJ-2"
    make_project(gone)
    shutil.rmtree(gone)
    row = _row(_store(tmp_path / "data.db"), check="references")
    detail = str(row["detail"])
    assert row["title"] == "references"
    assert f"project PROJ-1 at {present}" in detail
    assert f"project PROJ-2: {gone} is gone" in detail
    assert row["status"] == "warn"  # a missing project still holds what it held


def test_a_project_with_a_repair_pending_warns(tmp_path: Path) -> None:
    refs.register([])
    project = make_project(tmp_path / "PROJ-4")
    ref = refs.read_reference(refs.reference_file(RefHolder(kind="project", name=project.id)))
    write_reference(
        ref.holder,  # type: ignore[arg-type]
        ref.targets,
        anchor=ref.anchor,
        anchor_id=ref.anchor_id,
        moved_from=[tmp_path / "OLD"],
    )
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert f"moved from {tmp_path / 'OLD'}, and a plugin's repair is still pending" in str(
        row["detail"]
    )
    assert row["status"] == "warn"


def test_an_unreadable_project_is_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register([])
    away = tmp_path / "PROJ-3"
    make_project(away)
    shutil.rmtree(away)
    # Its parent now reads as another device: an ejected drive, not a deleted folder.
    real_stat = Path.stat

    def stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        found = real_stat(self, follow_symlinks=follow_symlinks)
        if self == tmp_path:
            fields = list(found)
            fields[2] = found.st_dev + 1  # st_dev
            return os.stat_result(fields)
        return found

    monkeypatch.setattr(Path, "stat", stat)
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert f"project PROJ-3: {away} can't be read now" in str(row["detail"])
    assert row["status"] == "pass"


def test_a_newer_reference_warns(tmp_path: Path) -> None:
    refs.register([])
    (refs.REFS.place / "install-future.json").write_text(
        '{"untaped": {"file": 2}, "holder": {"kind": "install", "name": "x"}}'
    )
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert row["status"] == "warn"
    assert "written by a newer untaped; upgrade this install to prune" in str(row["detail"])


def test_an_unlistable_refs_folder_warns_in_both_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    (tmp_path / "data.v3.db").write_bytes(b"x")
    real = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self == refs.REFS.place:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    references = _row(_store(tmp_path / "data.db"), check="references")
    assert references["status"] == "warn"
    assert "the folder cannot be read (Permission denied), so nothing is pruned" in str(
        references["detail"]
    )
    stores = _row(_store(tmp_path / "data.db"))
    assert stores["status"] == "warn"
    detail = str(stores["detail"])
    assert "layout 3 at" in detail and "holders unknown: the install references in" in detail
    assert stores["fix"] is None  # registering would not help: check the folder
    assert "not registered" not in str(references["detail"])


def test_a_layout_the_store_keeps_passes_with_why(tmp_path: Path) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    (tmp_path / "data.v3.db").write_bytes(b"x")
    store = Store(
        "acme", "data", base=lambda: tmp_path / "data.db", layout=4, keep=lambda _: "a profile"
    )
    row = _row(store)
    assert row["status"] == "pass"
    assert "layout 3 at" in str(row["detail"])
    assert str(row["detail"]).endswith("held by nobody: a profile")


def test_a_kept_layout_still_names_its_damaged_lock(tmp_path: Path) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    old = tmp_path / "data.v3.db"
    old.write_bytes(b"x")
    Path(f"{old}.inuse").write_bytes(b"garbage, not a database")
    store = Store(
        "acme", "data", base=lambda: tmp_path / "data.db", layout=4, keep=lambda _: "a profile"
    )
    row = _row(store)
    assert row["status"] == "warn"
    detail = str(row["detail"])
    assert f"held by nobody: a profile, and its in-use lock {old}.inuse is damaged" in detail


def test_a_layout_with_unknown_holders_still_names_its_damaged_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=4)])
    old = tmp_path / "data.v3.db"
    old.write_bytes(b"x")
    Path(f"{old}.inuse").write_bytes(b"garbage, not a database")
    real = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self == refs.REFS.place:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    detail = str(row["detail"])
    assert "holders unknown: the install references in" in detail
    assert f"and its in-use lock {old}.inuse is damaged" in detail
    assert row["fix"] is None  # the damaged lock's text says what to delete


def test_a_keep_check_that_fails_warns(tmp_path: Path) -> None:
    def broken(_path: Path) -> str | None:
        raise RuntimeError("no config")

    (tmp_path / "data.v3.db").write_bytes(b"x")
    store = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=4, keep=broken)
    row = _row(store)
    assert row["status"] == "warn"
    assert "its keep check failed (RuntimeError: no config)" in str(row["detail"])


def test_keep_must_be_a_function() -> None:
    with pytest.raises(ConfigError, match="keep must be a function of a path"):
        Store("acme", "data", base=lambda: Path("x.db"), keep="x")  # type: ignore[arg-type]
