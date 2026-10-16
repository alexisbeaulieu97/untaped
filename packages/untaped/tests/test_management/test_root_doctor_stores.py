"""The ``stores`` doctor row: each composed store's layout, and any other layout left on disk."""

from __future__ import annotations

from pathlib import Path

from untaped import bootstrap
from untaped.management.doctor import collect_doctor_rows
from untaped.plugins.registry import PluginSpec
from untaped.store import Store
from untaped.testing import plugin_candidate


def _row(*stores: Store) -> dict[str, object]:
    spec = PluginSpec(name="acme", stores=stores)
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec)])
    [row] = [
        row for row in collect_doctor_rows(bootstrap.SHELL_SPEC, result) if row["check"] == "stores"
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
    assert "is renamed to it on its next open" in str(row["detail"])


def test_another_layout_on_disk_warns(tmp_path: Path) -> None:
    (tmp_path / "data.v4.db").write_bytes(b"x")
    (tmp_path / "data.v3.db").write_bytes(b"xyz")
    row = _row(_store(tmp_path / "data.db"))
    assert row["status"] == "warn"
    assert f"also layout 3 at {tmp_path / 'data.v3.db'} (3 B)" in str(row["detail"])


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
