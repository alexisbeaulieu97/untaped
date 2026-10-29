"""``untaped recipe backups``: listing, showing, restoring and pruning backup bundles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import untaped_recipe.infrastructure.file_writer as file_writer_module
from recipe_pack_support import default_columns
from recipe_test_support import library_dir, run
from untaped.sdk.testing import ScriptedTerminal
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.backup import BackupDraft, BackupStore


def _config_backup(tmp_path: Path) -> tuple[BackupDraft, Path]:
    """Back up ``target/config.yml`` (before -> after) and leave it at ``after``."""
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.yml"
    change = FileChange(
        target=target, relative_path=Path("config.yml"), before="before\n", after="after\n"
    )
    config.write_text("before\n")
    bundle = _create_backup(
        BackupStore(library_dir() / "backups"),
        recipe_name="demo",
        inputs={"service": "api"},
        changes=[change],
    )
    config.write_text("after\n")
    return bundle, config


def test_backups_list_rows_carry_the_creation_time_and_recipe(tmp_path: Path) -> None:
    bundle, _config = _config_backup(tmp_path)

    listed = run(["backups", "list", "--format", "json"])
    columns = run(["backups", "list", "--columns", "?"])

    assert listed.exit_code == 0, listed.output
    # The id's UTC stamp, rendered in full with a ``Z``.
    stamp = datetime.strptime(bundle.id.partition("-")[0], "%Y%m%dT%H%M%S%fZ")
    created_at = stamp.isoformat() + "Z"
    assert json.loads(listed.stdout) == [
        {
            "id": bundle.id,
            "created_at": created_at,
            "recipe": "demo",
            "path": str(bundle.path),
            # One recipe.backup schema: what only ``backups get`` reads is null here.
            "inputs": None,
            "files": None,
        }
    ]
    assert default_columns(columns.stderr) == {"id", "created_at", "recipe"}


def test_backups_list_warns_about_unreadable_metadata(tmp_path: Path) -> None:
    bundle, _config = _config_backup(tmp_path)
    (bundle.path / "metadata.json").write_text("{")

    listed = run(["backups", "list", "--format", "json"])

    assert listed.exit_code == 0, listed.output
    row = json.loads(listed.stdout)[0]
    assert (row["id"], row["recipe"], row["path"]) == (bundle.id, None, str(bundle.path))
    assert row["created_at"].endswith("Z")
    assert f"invalid backup metadata: {bundle.id}" in listed.stderr


def test_backup_commands_show_list_and_restore(tmp_path: Path) -> None:
    bundle, config = _config_backup(tmp_path)

    listed = run(["backups", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)[0]["id"] == bundle.id
    shown = run(["backups", "get", bundle.id])
    assert shown.exit_code == 0, shown.output
    assert "recipe: demo" in shown.stdout
    assert str(config.parent) in shown.stdout
    assert config.name in shown.stdout
    assert "[{" not in shown.stdout
    shown_json = run(["backups", "get", bundle.id, "--format", "json"])
    assert shown_json.exit_code == 0, shown_json.output
    shown_record = json.loads(shown_json.stdout)
    assert list(shown_record) == ["id", "created_at", "recipe", "path", "inputs", "files"]
    assert (shown_record["id"], shown_record["path"]) == (bundle.id, str(bundle.path))
    assert [entry["target"] for entry in shown_record["files"]] == [str(config.parent)]

    refused = run(["backups", "restore", bundle.id])
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert config.read_text() == "after\n"
    restored = run(["backups", "restore", bundle.id, "--yes"])
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"


def test_backup_restore_dry_run_and_decline_change_nothing(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    bundle, config = _config_backup(tmp_path)

    dry_run = run(["backups", "restore", bundle.id, "--dry-run"])
    terminal.answers(False)
    declined = run(["backups", "restore", bundle.id])

    assert dry_run.exit_code == 0, dry_run.output
    assert "About to restore 1 file:" in dry_run.stderr
    # A declined confirmation exits 1 with the standard decline line.
    assert declined.exit_code == 1, declined.output
    assert "cancelled; no changes made" in declined.stderr
    assert "restored" not in declined.stderr
    assert config.read_text() == "after\n"


def test_backup_restore_emits_an_outcome_record(tmp_path: Path) -> None:
    bundle, config = _config_backup(tmp_path)
    restore = ["backups", "restore", bundle.id]

    planned = run([*restore, "--dry-run", "--format", "json"])
    assert config.read_text() == "after\n"
    restored = run([*restore, "--yes", "--format", "json"])

    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.stdout) == {
        "id": bundle.id,
        "files": 1,
        "detail": None,
        "action": "planned",
    }
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.stdout) == {
        "id": bundle.id,
        "files": 1,
        "detail": None,
        "action": "restored",
    }
    assert config.read_text() == "before\n"


@pytest.mark.parametrize("selector", ["latest", "prefix"])
def test_backup_restore_record_carries_the_resolved_bundle_id(
    tmp_path: Path, selector: str
) -> None:
    bundle, _config = _config_backup(tmp_path)
    backup_id = "latest" if selector == "latest" else bundle.id[:15]

    result = run(["backups", "restore", backup_id, "--yes", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "id": bundle.id,
        "files": 1,
        "detail": None,
        "action": "restored",
    }


def test_backup_restore_yaml_is_one_mapping(tmp_path: Path) -> None:
    bundle, _config = _config_backup(tmp_path)

    result = run(["backups", "restore", bundle.id, "--dry-run", "--format", "yaml"])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == {
        "id": bundle.id,
        "files": 1,
        "detail": None,
        "action": "planned",
    }


def test_backup_restore_table_output_is_the_outcome_and_the_success_line(tmp_path: Path) -> None:
    bundle, _config = _config_backup(tmp_path)

    result = run(["backups", "restore", bundle.id, "--yes"])

    assert result.exit_code == 0, result.output
    assert "action: restored" in result.stdout
    assert f"restored {bundle.id}" in result.stderr


def test_backup_restore_failing_item_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    first = target / "one.txt"
    second = target / "two.txt"
    first.write_text("one-before\n")
    second.write_text("two-before\n")
    store = BackupStore(library_dir() / "backups")
    bundle = _create_backup(
        store,
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("one.txt"),
                before="one-before\n",
                after="one-after\n",
            ),
            FileChange(
                target=target,
                relative_path=Path("two.txt"),
                before="two-before\n",
                after="two-after\n",
            ),
        ],
    )
    first.write_text("one-after\n")
    second.write_text("two-after\n")
    original_replace = file_writer_module.os.replace

    def fail_second_replace(source: Path, dest: Path) -> None:
        if Path(dest).name == "two.txt":
            raise OSError("disk full")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_replace)

    result = run(["backups", "restore", bundle.id, "--yes", "-f", "json"])

    assert result.exit_code == 1, result.output
    assert "disk full" in result.output
    row = json.loads(result.stdout)
    assert (row["id"], row["files"], row["action"]) == (bundle.id, 2, "failed")
    assert "disk full" in row["detail"]
    assert row["error"]["message"] == row["detail"]
    # The restore is one staged transaction: a mid-write failure rolls back
    # already-restored files, so both keep their pre-restore content.
    assert first.read_text() == "one-after\n"
    assert second.read_text() == "two-after\n"


def _seed_bundle(backups_root: Path, bundle_id: str, *, payload_bytes: int = 10) -> Path:
    bundle_dir = backups_root / bundle_id
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "0").write_text("x" * payload_bytes)
    (bundle_dir / "metadata.json").write_text(
        json.dumps({"id": bundle_id, "recipe": "demo", "inputs": {}, "files": []})
    )
    return bundle_dir


@pytest.mark.parametrize(
    ("args", "env_keep", "survivors"),
    [
        pytest.param(["--keep", "2"], None, {"b", "c"}, id="keep-prunes-oldest"),
        pytest.param(["--older-than", "30"], None, {"b", "c"}, id="older-than-days"),
        pytest.param(["--keep", "1", "--older-than", "30"], None, {"c"}, id="union"),
        pytest.param([], "1", {"c"}, id="settings-when-flags-absent"),
    ],
)
def test_backup_prune_policies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    env_keep: str | None,
    survivors: set[str],
) -> None:
    backups = library_dir() / "backups"
    ids = {
        "a": "20200101T000000000000Z-aaaaaaaa",
        "b": "20990201T000000000000Z-bbbbbbbb",
        "c": "20990301T000000000000Z-cccccccc",
    }
    for bundle_id in ids.values():
        _seed_bundle(backups, bundle_id)
    if env_keep is not None:
        monkeypatch.setenv("UNTAPED_RECIPE__BACKUP_KEEP", env_keep)

    result = run(["backups", "prune", *args, "--yes"])

    assert result.exit_code == 0, result.output
    assert {key for key, bundle_id in ids.items() if (backups / bundle_id).exists()} == survivors
    pruned = len(ids) - len(survivors)
    assert f"pruned {pruned} of 3 backups" in result.stderr
    assert "reclaimed" in result.stderr
    assert ids["a"] in result.stdout


def test_backup_prune_requires_a_policy(tmp_path: Path) -> None:
    _seed_bundle(library_dir() / "backups", "20250101T000000000000Z-aaaaaaaa")

    result = run(["backups", "prune", "--yes"])

    assert result.exit_code != 0
    assert "backups prune needs --keep/--older-than" in result.output


def test_backup_prune_asks_first_and_keeps_everything_when_declined(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    backups = library_dir() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    new = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    piped = run(["backups", "prune", "--keep", "1"])

    assert piped.exit_code == 2, piped.output
    assert "requires --yes" in piped.output
    assert old.exists() and new.exists()

    terminal.answers(False)
    declined = run(["backups", "prune", "--keep", "1"])

    assert declined.exit_code == 1, declined.output
    assert "cancelled; no changes made" in declined.output
    assert [kind for kind, _ in terminal.asked] == ["confirm"]
    assert old.exists() and new.exists()


def test_backup_prune_counts_failed_deletions_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backups = library_dir() / "backups"
    first = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    second = _seed_bundle(backups, "20250201T000000000000Z-bbbbbbbb")
    _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    original_delete = BackupStore.delete

    def flaky_delete(self: BackupStore, backup_id: str) -> None:
        if backup_id.endswith("aaaaaaaa"):
            raise ValueError(f"backup not found: {backup_id}")
        original_delete(self, backup_id)

    monkeypatch.setattr(BackupStore, "delete", flaky_delete)

    result = run(["backups", "prune", "--keep", "1", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert [item for item, _ in result.failures] == ["20250101T000000000000Z-aaaaaaaa"]
    assert first.exists()
    assert not second.exists()
    failed, deleted = json.loads(result.stdout)
    assert deleted["size_bytes"] > 0
    assert (failed["id"], failed["action"]) == (first.name, "failed")
    assert failed["detail"] == f"backup not found: {first.name}"
    assert failed["error"]["message"] == failed["detail"]
    assert deleted == {
        "id": second.name,
        "size_bytes": deleted["size_bytes"],
        "detail": None,
        "action": "deleted",
    }


def test_backup_prune_rows_say_planned_or_deleted(tmp_path: Path) -> None:
    backups = library_dir() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    _seed_bundle(backups, "20990301T000000000000Z-cccccccc")
    args = ["backups", "prune", "--keep", "1", "--yes", "-f", "pipe"]

    planned = run([*args, "--dry-run"])
    deleted = run(args)

    for result, action in ((planned, "planned"), (deleted, "deleted")):
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout)
        assert envelope["kind"] == "recipe.prune_outcome"
        assert (envelope["record"]["id"], envelope["record"]["action"]) == (old.name, action)
    assert not old.exists()


def _create_backup(
    store: BackupStore,
    *,
    recipe_name: str,
    inputs: dict[str, object],
    changes: list[FileChange],
) -> BackupDraft:
    draft = store.start(recipe_name=recipe_name, inputs=inputs)
    draft.commit(draft.stage(changes, inputs=inputs))
    return draft
