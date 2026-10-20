"""``setup migrate-dirs`` rows for what the automatic cleanup leaves: layouts, dead references.

One rule: every layout nobody holds that the cleanup will not remove on its
own is one ``<plugin>.<store>-layouts`` row with the reason; a dead
reference file is core's ``untaped.references`` row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from test_management.support import make_spec
from untaped import bootstrap, refs
from untaped.migrations import other_layouts_migration, setting_guard
from untaped.plugins.registry import (
    MigrationOptions,
    PluginContext,
    PluginSpec,
    named_plugin_dir,
)
from untaped.refs import RefTarget
from untaped.store import Store, layout_path
from untaped.testing import CliResult, invoke_cli, plugin_candidate, write_reference

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _base() -> Path:
    return named_plugin_dir("acme") / "data.db"


STORE = Store("acme", "data", base=_base, layout=5)


def _spec(store: Store = STORE) -> PluginSpec:
    migration = other_layouts_migration(store)
    return replace(make_spec("acme"), stores=(store,), migrations=(migration,))


def _cli(*args: str, spec: PluginSpec | None = None) -> CliResult:
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec or _spec())])
    return invoke_cli(root.meta, ["setup", "migrate-dirs", "--format", "json", *args])


def _layout(layout: int, content: bytes = b"data") -> Path:
    path = layout_path(_base(), layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_an_unheld_layout_on_an_unregistered_machine_is_a_delete_row() -> None:
    old = _layout(4, b"x" * 7)
    Path(f"{old}-journal").write_bytes(b"j")
    _layout(5)

    rows = json.loads(_cli("--dry-run").stdout)

    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert (row["action"], row["source"], row["bytes"]) == ("delete", str(old), 7)
    assert row["detail"] == (
        "layout 4, held by nobody: no registered install keeps this store, so nothing cleans up"
    )


def test_applying_removes_the_layout_and_everything_that_travels_with_it() -> None:
    old = _layout(4)
    Path(f"{old}-journal").write_bytes(b"j")

    result = _cli("--yes")

    assert result.exit_code == 0, result.stderr
    outcome = next(row for row in json.loads(result.stdout) if row["id"] == "acme.data-layouts")
    assert outcome["action"] == "deleted"
    assert not old.exists() and not Path(f"{old}-journal").exists()
    assert not any(".removing-" in path.name for path in old.parent.iterdir())


def test_a_layout_the_next_fill_removes_is_not_listed() -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=5)])
    _layout(4)
    rows = json.loads(_cli("--dry-run").stdout)
    assert [row for row in rows if row["id"] == "acme.data-layouts"] == []


def test_a_held_layout_is_not_listed(tmp_path: Path) -> None:
    other = tmp_path / "tool"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        "install:aaaaaaaaaaaa",
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=other,
        version="11.1.0",
    )
    _layout(4)
    rows = json.loads(_cli("--dry-run").stdout)
    assert [row for row in rows if row["id"] == "acme.data-layouts"] == []


def test_a_layout_a_running_process_has_open_is_listed_and_fails_to_apply() -> None:
    refs.register([RefTarget(plugin="acme", store="data", layout=5)])
    old = _layout(4)
    Store("acme", "data", base=_base, layout=4).open()  # held as another process would

    rows = json.loads(_cli("--dry-run").stdout)
    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert row["detail"] == "layout 4, held by nobody: a running untaped has it open"

    result = _cli("--yes")
    outcome = next(row for row in json.loads(result.stdout) if row["id"] == "acme.data-layouts")
    assert outcome["action"] == "failed"
    assert outcome["detail"] == "~/.untaped/plugins/acme/data.v4.db is open in a running untaped"
    assert old.exists()


def test_a_damaged_lock_does_not_stop_a_person_who_chose_to_remove_it() -> None:
    old = _layout(4)
    Path(f"{old}.inuse").write_bytes(b"not a database")
    refs.register([RefTarget(plugin="acme", store="data", layout=5)])
    rows = json.loads(_cli("--dry-run").stdout)
    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert "is damaged" in row["detail"]
    assert _cli("--yes").exit_code == 0
    assert not old.exists() and not Path(f"{old}.inuse").exists()


def test_a_layout_a_profile_points_at_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _layout(4)
    monkeypatch.setenv("UNTAPED_ACME__INDEX_PATH", str(old))
    store = Store("acme", "data", base=_base, layout=5, keep=setting_guard("acme", "index_path"))
    spec = _spec(store)
    rows = json.loads(_cli("--dry-run", spec=spec).stdout)
    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert row["action"] == "keep"
    assert "still sets acme.index_path there" in row["detail"]


def test_a_symlinked_layout_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    target.write_bytes(b"x")
    link = layout_path(_base(), 4)
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    rows = json.loads(_cli("--dry-run").stdout)
    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert row["action"] == "keep"
    assert target.exists()


def test_nothing_to_show_on_a_clean_home() -> None:
    _layout(5)
    rows = json.loads(_cli("--dry-run").stdout)
    assert [row["id"] for row in rows] == []


# --- dead references -------------------------------------------------------------


def test_a_dead_reference_is_cores_row_and_applying_removes_it(tmp_path: Path) -> None:
    file = write_reference(
        "install:aaaaaaaaaaaa",
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=tmp_path / "gone",
        version="11.0.0",
    )
    rows = json.loads(_cli("--dry-run").stdout)
    [row] = [row for row in rows if row["id"] == "untaped.references"]
    assert (row["action"], row["source"]) == ("delete", str(file))
    assert row["detail"] == f"untaped 11.0.0 at {tmp_path / 'gone'}: no untaped there any more"

    result = _cli("--yes")
    assert result.exit_code == 0, result.stderr
    assert not file.exists()


def test_a_reference_of_an_unknown_kind_is_never_offered(tmp_path: Path) -> None:
    file = write_reference(  # a later untaped's kind; an install anchored here would be dead
        "project:pay",
        [RefTarget(plugin="git", store="store", item="github.com/acme/pay")],
        anchor=tmp_path / "gone",
    )
    rows = json.loads(_cli("--dry-run").stdout)
    assert [row for row in rows if row["id"] == "untaped.references"] == []
    assert _cli("--yes").exit_code == 0
    assert file.exists()


def test_a_live_reference_is_no_row(tmp_path: Path) -> None:
    alive = tmp_path / "tool"
    (alive / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        "install:aaaaaaaaaaaa",
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=alive,
        version="11.1.0",
    )
    rows = json.loads(_cli("--dry-run").stdout)
    assert [row for row in rows if row["id"] == "untaped.references"] == []


def test_applying_checks_again_what_holds_each_layout(tmp_path: Path) -> None:
    """A registration landing after the preview is seen at apply: nothing is removed."""
    old = _layout(4)
    preview = json.loads(_cli("--dry-run").stdout)
    assert [row["action"] for row in preview if row["id"] == "acme.data-layouts"] == ["delete"]
    other = tmp_path / "tool"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        "install:aaaaaaaaaaaa",
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=other,
        version="11.1.0",
    )

    migration = other_layouts_migration(STORE)
    applied = migration.apply(PluginContext(settings=None), MigrationOptions())

    assert [outcome.action for outcome in applied] == ["unchanged"]
    assert old.exists()


def test_an_unlistable_refs_folder_keeps_every_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "tool"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    write_reference(
        "install:aaaaaaaaaaaa",
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=other,
        version="11.1.0",
    )
    old = _layout(4)
    real = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self == refs.refs_dir():
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    rows = json.loads(_cli("--dry-run").stdout)
    [row] = [row for row in rows if row["id"] == "acme.data-layouts"]
    assert row["action"] == "keep"
    assert row["detail"].startswith("kept: who holds it is unknown: the install references in")
    assert _cli("--yes").exit_code == 0
    assert old.exists()


@pytest.mark.parametrize("unknown", ["folder", "newer"])
def test_a_damaged_lock_never_hides_unknown_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: str
) -> None:
    other = tmp_path / "tool"
    (other / "untaped-11.1.0.dist-info").mkdir(parents=True)
    old = _layout(4)
    Path(f"{old}.inuse").write_bytes(b"not a database")
    if unknown == "newer":  # a newer untaped's reference, which may name layout 4
        refs.refs_dir().mkdir(parents=True)
        (refs.refs_dir() / "install-future.json").write_text('{"untaped": "99", "holder": "x"}')
    else:  # a live install's reference names layout 4, in a folder this run cannot list
        write_reference(
            "install:aaaaaaaaaaaa",
            [RefTarget(plugin="acme", store="data", layout=4)],
            anchor=other,
            version="11.1.0",
        )
        real = Path.iterdir

        def iterdir(self: Path) -> Iterator[Path]:
            if self == refs.refs_dir():
                raise PermissionError(13, "Permission denied")
            return real(self)

        monkeypatch.setattr(Path, "iterdir", iterdir)
    [row] = [
        row for row in json.loads(_cli("--dry-run").stdout) if row["id"] == "acme.data-layouts"
    ]
    assert row["action"] == "keep" and "who holds it is unknown" in row["detail"]
    assert _cli("--yes").exit_code == 0
    assert old.exists()


def test_a_damaged_lock_never_hides_what_keep_names(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _layout(4)
    Path(f"{old}.inuse").write_bytes(b"not a database")
    store = Store("acme", "data", base=_base, layout=5, keep=lambda _: "a profile points here")
    [row] = [
        row
        for row in json.loads(_cli("--dry-run", spec=_spec(store)).stdout)
        if row["id"] == "acme.data-layouts"
    ]
    assert (row["action"], row["detail"]) == ("keep", "kept: a profile points here")
