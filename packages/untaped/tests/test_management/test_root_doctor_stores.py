"""The ``stores`` and ``references`` doctor rows: each layout on disk, and who holds it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped import bootstrap, refs
from untaped.management.doctor import collect_doctor_rows
from untaped.plugins.registry import PluginSpec
from untaped.store import Store
from untaped.testing import plugin_candidate, write_reference


def _install(root: Path, version: str = "11.1.0") -> Path:
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


def _row(*stores: Store, check: str = "stores") -> dict[str, object]:
    spec = PluginSpec(name="acme", stores=stores)
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec)])
    [row] = [
        row for row in collect_doctor_rows(bootstrap.SHELL_SPEC, result) if row["check"] == check
    ]
    return row


def _store(base: Path, *, layout: int = 4, bare: int | None = None) -> Store:
    return Store("acme", "data", base=lambda: base, layout=layout, bare=bare)


def test_a_store_not_created_yet_passes(tmp_path: Path) -> None:
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert row["detail"] == f"acme.data: layout 4 at {tmp_path / 'data.v4.db'}, not created yet"


def test_one_layout_on_disk_passes_with_its_size(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x" * 2048)
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert row["detail"] == f"acme.data: layout 4 at {tmp_path / 'data.v4.db'} (2.0 KiB)"


def test_a_bare_file_awaiting_its_rename_passes(tmp_path: Path) -> None:
    (tmp_path / "data.db").write_bytes(b"x")
    row = _row(_store(tmp_path / "data.db", bare=4))
    assert row["status"] == "pass"
    assert "data.db (1 B) is renamed to layout 4 on its next open" in str(row["detail"])


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
    refs.register(["acme.data@4"])
    checkout = _install(tmp_path / "checkout")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@3"], anchor=checkout, version="11.1.0")
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert "(1 B), held by this install;" in str(row["detail"])
    assert f"(3 B), held by untaped 11.1.0 at {checkout}" in str(row["detail"])


def test_an_unheld_layout_the_next_fill_removes_passes(tmp_path: Path) -> None:
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    refs.register(["acme.data@4"])
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "pass"
    assert "held by nobody (removed at the next complete fill)" in str(row["detail"])


def test_a_layout_a_running_process_has_open_warns(tmp_path: Path) -> None:
    old = tmp_path / "data.v3.db"
    old.write_bytes(b"xyz")
    refs.register(["acme.data@4"])
    reader = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=3)
    reader.open()  # this process reads layout 3 as another install would
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert "held by nobody: a running untaped has it open" in str(row["detail"])
    assert row["fix"] == ["--profile", "default", "setup", "migrate-dirs"]
    assert row["automatic"] is False


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
    refs.register(["acme.data@4"])
    (refs.refs_dir() / "install-future.json").write_text('{"untaped": "2"}')
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
    refs.register(["acme.data@4"])
    gone = tmp_path / "gone"
    old = _install(tmp_path / "old", "11.2.0")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@3"], anchor=gone, version="11.0.0")
    write_reference("install:bbbbbbbbbbbb", ["acme.data@3"], anchor=old, version="11.1.0")
    write_reference("workspace:pay", ["git.store#github.com/acme/pay"], plugin="workspace")
    row = _row(_store(tmp_path / "data.db"), check="references")
    detail = str(row["detail"])
    assert row["status"] == "pass"  # a dead reference is information, not a warning
    assert f"untaped 11.0.0 at {gone}: no untaped there any more" in detail
    assert f"untaped 11.1.0 at {old}: reference from before an upgrade" in detail
    assert "workspace:pay (plugin workspace)" in detail
    assert "(this install)" in detail


def test_a_newer_reference_warns(tmp_path: Path) -> None:
    refs.register([])
    (refs.refs_dir() / "install-future.json").write_text('{"untaped": "2", "holder": "x"}')
    row = _row(_store(tmp_path / "data.db"), check="references")
    assert row["status"] == "warn"
    assert "written by a newer untaped; upgrade this install to prune" in str(row["detail"])


def test_an_unlistable_refs_folder_warns_in_both_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs.register(["acme.data@4"])
    (tmp_path / "data.v3.db").write_bytes(b"x")
    real = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self == refs.refs_dir():
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
    assert "a reference this untaped cannot read pauses cleanup" in str(stores["detail"])


def test_a_layout_the_store_keeps_passes_with_why(tmp_path: Path) -> None:
    refs.register(["acme.data@4"])
    (tmp_path / "data.v3.db").write_bytes(b"x")
    store = Store(
        "acme", "data", base=lambda: tmp_path / "data.db", layout=4, keep=lambda _: "a profile"
    )
    row = _row(store)
    assert row["status"] == "pass"
    assert "layout 3 at" in str(row["detail"])
    assert str(row["detail"]).endswith("held by nobody: a profile")
