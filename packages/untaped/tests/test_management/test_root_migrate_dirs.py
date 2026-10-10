"""``untaped setup migrate-dirs``: plugins' rows compose, preview, confirm and apply (S38).

Two toy plugins stand in for any plugin's rows: ``alpha`` moves a planted
directory into its own, ``beta`` deletes one with ``delete_migration``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from test_management.support import make_spec, write_config
from untaped import bootstrap
from untaped.management.doctor import collect_doctor_rows
from untaped.management.plugin_check import check_plugins
from untaped.plugins.registry import (
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    PluginSpec,
)
from untaped.sdk import delete_migration, old_dirs, plugin_dir, retired_values
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

CALLS: list[str] = []


def _old(name: str) -> Path:
    return Path.home() / ".untaped" / f"{name}-old"


def _move_row(name: str, *, raises: bool = False) -> DirMigration:
    """A row moving ``~/.untaped/<name>-old`` to ``plugins/<name>/data``."""
    spec_dir = Path.home() / ".untaped" / "plugins" / name / "data"

    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        if not _old(name).is_dir():
            return []
        return [
            MigrationRow(action="move", source=str(_old(name)), destination=str(spec_dir), bytes=3)
        ]

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        CALLS.append(name)
        if raises:
            raise RuntimeError("boom")
        if not _old(name).is_dir():
            return [MigrationOutcome(id=f"{name}.data", action="unchanged")]
        spec_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(_old(name), spec_dir)
        return [MigrationOutcome(id=f"{name}.data", action="moved")]

    return DirMigration(id=f"{name}.data", title="old data", preview=preview, apply=apply)


def _spec(name: str, *migrations: DirMigration) -> PluginSpec:
    return replace(make_spec(name), migrations=migrations)


def _specs(*, raises: bool = False) -> tuple[PluginSpec, ...]:
    gone = delete_migration("beta.cache", "old cache", lambda: [_old("beta")], detail="old")
    # Declared out of order: the registry sorts by name.
    return (_spec("beta", gone), _spec("alpha", _move_row("alpha", raises=raises)))


def _plant() -> None:
    CALLS.clear()
    for name in ("alpha", "beta"):
        _old(name).mkdir(parents=True)
        (_old(name) / "file").write_text("abc", encoding="utf-8")


def _run(*args: str, specs: Sequence[PluginSpec] = (), **kwargs: object) -> CliResult:
    root = bootstrap.build_root_app(
        candidates=[plugin_candidate(spec) for spec in (specs or _specs())]
    )
    return invoke_cli(root.meta, ["setup", "migrate-dirs", *args], **kwargs)  # type: ignore[arg-type]


def test_dry_run_previews_every_plugins_rows_in_registry_order_and_changes_nothing() -> None:
    _plant()

    result = _run("--dry-run", "--format", "json")

    assert result.exit_code == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [(row["id"], row["action"]) for row in rows] == [
        ("alpha.data", "move"),
        ("beta.cache", "delete"),
    ]
    assert rows[1]["source"] == str(_old("beta")) and rows[1]["bytes"] == 3
    assert _old("alpha").is_dir() and _old("beta").is_dir()
    assert CALLS == []


def test_yes_applies_every_row_in_order_then_a_rerun_has_nothing_to_do() -> None:
    _plant()

    result = _run("--yes", "--format", "json")

    assert result.exit_code == 0, result.stderr
    outcomes = json.loads(result.stdout)
    assert [(row["id"], row["action"]) for row in outcomes] == [
        ("alpha.data", "moved"),
        ("beta.cache", "deleted"),
    ]
    assert CALLS == ["alpha"]
    assert not _old("alpha").exists() and not _old("beta").exists()
    assert (Path.home() / ".untaped" / "plugins" / "alpha" / "data" / "file").is_file()

    again = _run("--yes", "--format", "json")
    assert again.exit_code == 0
    assert json.loads(again.stdout) == []
    assert "nothing to migrate" in again.stderr


def test_a_row_that_raises_is_a_failed_outcome_and_the_others_still_run() -> None:
    _plant()

    result = _run("--yes", "--format", "json", specs=_specs(raises=True))

    assert result.exit_code == 1
    outcomes = json.loads(result.stdout)
    assert [(row["id"], row["action"]) for row in outcomes] == [
        ("alpha.data", "failed"),
        ("beta.cache", "deleted"),
    ]
    assert "RuntimeError: boom" in outcomes[0]["detail"]
    assert not _old("beta").exists()


def test_without_a_terminal_it_asks_for_yes() -> None:
    _plant()

    result = _run()

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert _old("beta").is_dir()


def test_a_declined_confirmation_changes_nothing() -> None:
    _plant()
    backend = ScriptedPromptBackend(confirms=[False])

    result = _run(interactive=True, prompt_backend=backend, terminal=True)

    assert result.exit_code != 0
    assert "About to migrate:" in result.stderr
    assert _old("alpha").is_dir() and _old("beta").is_dir()
    assert CALLS == []


@pytest.mark.parametrize(
    ("ids", "reason"),
    [(("alpha.data", "alpha.data"), "duplicate-migration"), (("data",), "bad-migration")],
)
def test_a_bad_or_repeated_row_id_quarantines_the_plugin(ids: tuple[str, ...], reason: str) -> None:
    rows = [replace(_move_row("alpha"), id=row_id) for row_id in ids]
    specs = [_spec("alpha", *rows), _spec("beta")]
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in specs])

    assert [record.reason for record in result.quarantine] == [reason]


def test_doctor_warns_while_anything_is_left_and_passes_after() -> None:
    _plant()
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in _specs()])

    def row() -> dict[str, object]:
        (found,) = [
            r
            for r in collect_doctor_rows(bootstrap.SHELL_SPEC, result)
            if r["check"] == "migrate-dirs"
        ]
        return found

    before = row()
    assert before["status"] == "warn"
    assert "2 directories to migrate" in str(before["detail"])
    assert before["fix"] == ["--profile", "default", "setup", "migrate-dirs"]

    assert _run("--yes").exit_code == 0
    assert row()["status"] == "pass"


def test_plugin_check_runs_each_preview_on_an_empty_home() -> None:
    _plant()
    seen: list[bool] = []

    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        seen.append(_old("alpha").exists())
        return []

    spec = _spec("alpha", replace(_move_row("alpha"), preview=preview))
    candidates = [plugin_candidate(spec)]
    rows = check_plugins(bootstrap.compose_root(candidates=candidates), candidates, "alpha")

    (row,) = [row for row in rows if row.check == "migrations"]
    assert row.status == "pass", row.detail
    assert seen == [False]
    assert _old("alpha").is_dir()


def test_plugin_check_fails_a_preview_returning_the_wrong_type() -> None:
    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        return ["move it"]  # type: ignore[list-item]

    spec = _spec("alpha", replace(_move_row("alpha"), preview=preview))
    candidates = [plugin_candidate(spec)]
    rows = check_plugins(bootstrap.compose_root(candidates=candidates), candidates, "alpha")

    (row,) = [row for row in rows if row.check == "migrations"]
    assert row.status == "fail"


def test_a_deleted_keys_values_are_still_found_in_every_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  work:\n    alpha:\n      cache_dir: ~/two\n"
        "  default:\n    alpha:\n      cache_dir: ~/one\n"
        "  home:\n    alpha:\n      cache_dir: ~/one\n",
    )

    assert retired_values("alpha", "cache_dir") == ("~/one", "~/two")
    assert old_dirs("~/.untaped/alpha-cache", "alpha", "cache_dir") == [
        Path("~/.untaped/alpha-cache").expanduser(),
        Path("~/one").expanduser(),
        Path("~/two").expanduser(),
    ]


def test_delete_migration_never_deletes_home_or_above() -> None:
    row = delete_migration("alpha.x", "x", lambda: [Path.home(), Path("/")])
    ctx, options = PluginContext(settings=None), MigrationOptions()

    assert row.preview(ctx, options) == []
    assert [o.action for o in row.apply(ctx, options)] == ["unchanged"]
    assert Path.home().is_dir()


def test_plugin_dir_is_where_a_row_moves_data() -> None:
    assert plugin_dir(make_spec("alpha")) == Path.home() / ".untaped" / "plugins" / "alpha"


def _raising_preview(name: str) -> DirMigration:
    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        raise RuntimeError("cannot read")

    return replace(_move_row(name), preview=preview)


def test_a_preview_that_raises_is_a_failed_row_in_the_preview_doctor_and_check() -> None:
    _plant()
    specs = (_spec("alpha", _raising_preview("alpha")),)

    result = _run("--dry-run", "--format", "json", specs=specs)
    assert result.exit_code == 1
    (row,) = json.loads(result.stdout)
    assert (row["id"], row["action"]) == ("alpha.data", "failed")
    assert "RuntimeError: cannot read" in row["detail"]

    candidates = [plugin_candidate(spec) for spec in specs]
    composed = bootstrap.compose_root(candidates=candidates)
    (doctor,) = [
        r
        for r in collect_doctor_rows(bootstrap.SHELL_SPEC, composed)
        if r["check"] == "migrate-dirs"
    ]
    assert doctor["status"] == "warn" and "alpha.data" in str(doctor["detail"])
    (checked,) = [
        r for r in check_plugins(composed, candidates, "alpha") if r.check == "migrations"
    ]
    assert checked.status == "fail"


def test_rows_with_nothing_to_move_are_shown_without_asking() -> None:
    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        return [MigrationRow(action="keep", source="/somewhere", detail="kept")]

    specs = (_spec("alpha", replace(_move_row("alpha"), preview=preview)),)
    result = _run("--format", "json", specs=specs)

    assert result.exit_code == 0
    assert [row["action"] for row in json.loads(result.stdout)] == ["keep"]
    assert "nothing to move or delete" in result.stderr


def test_an_apply_returning_the_wrong_type_is_a_failed_outcome() -> None:
    _plant()

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        return "done"  # type: ignore[return-value]

    specs = (_spec("alpha", replace(_move_row("alpha"), apply=apply)),)
    result = _run("--yes", "--format", "json", specs=specs)

    assert result.exit_code == 1
    (outcome,) = json.loads(result.stdout)
    assert outcome["action"] == "failed" and "expected MigrationOutcome" in outcome["detail"]


def test_a_preview_returning_the_wrong_type_fails_its_row() -> None:
    def preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
        return {"action": "move"}  # type: ignore[return-value]

    specs = (_spec("alpha", replace(_move_row("alpha"), preview=preview)),)
    (row,) = json.loads(_run("--dry-run", "--format", "json", specs=specs).stdout)

    assert row["action"] == "failed" and "expected MigrationRow" in row["detail"]


def test_an_untaped_error_reads_as_its_own_message() -> None:
    from untaped.errors import UntapedError

    def apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
        raise UntapedError("the disk is full")

    _plant()
    specs = (_spec("alpha", replace(_move_row("alpha"), apply=apply)),)
    (outcome,) = json.loads(_run("--yes", "--format", "json", specs=specs).stdout)

    assert outcome["detail"] == "the disk is full"


def test_a_malformed_row_quarantines_the_plugin() -> None:
    spec = _spec("alpha", replace(_move_row("alpha"), preview="not callable"))  # type: ignore[arg-type]
    result = bootstrap.compose_root(candidates=[plugin_candidate(spec)])

    assert [record.reason for record in result.quarantine] == ["bad-migration"]


def test_dir_bytes_counts_files_and_tolerates_what_is_missing(tmp_path: Path) -> None:
    from untaped.sdk import dir_bytes

    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "sub" / "f").write_text("abcd", encoding="utf-8")
    (tmp_path / "file").write_text("ab", encoding="utf-8")

    assert dir_bytes(tmp_path / "d") == 4
    assert dir_bytes(tmp_path / "file") == 2
    assert dir_bytes(tmp_path / "missing") == 0


def test_old_dirs_skips_values_that_are_not_paths(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    alpha:\n      cache_dir: 3\n  work:\n    alpha:\n"
        "      cache_dir: ' '\n",
    )

    assert old_dirs("~/a", "alpha", "cache_dir") == [Path("~/a").expanduser()]


def test_delete_migration_reports_a_path_it_cannot_delete(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "f").write_text("x", encoding="utf-8")
    row = delete_migration("alpha.x", "x", lambda: [locked / "inner"])
    locked.chmod(0o500)
    try:
        (outcome,) = row.apply(PluginContext(settings=None), MigrationOptions())
    finally:
        locked.chmod(0o700)

    if outcome.action == "deleted":
        pytest.skip("running as root: a read-only parent doesn't stop the delete")
    assert outcome.action == "failed" and "could not delete" in outcome.detail


def test_shown_path_writes_home_as_a_tilde() -> None:
    from untaped.sdk import shown_path

    assert shown_path(Path.home()) == "~"
    assert shown_path(Path.home() / "x") == "~/x"
    assert shown_path("/elsewhere") == "/elsewhere"
