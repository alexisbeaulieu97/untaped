"""``untaped recipe packs``: adding, removing and showing packs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import untaped_recipe.infrastructure.pack_store as pack_store_module
from recipe_pack_support import install_pack, write_pack, write_pack_project
from recipe_test_support import library_dir, run
from untaped.sdk.testing import (
    ScriptedTerminal,
)
from untaped_recipe.infrastructure.pack_store import PackLibrary


def test_add_pack_installs_without_prompting_and_prints_summary(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)

    result = run(["packs", "add", str(pack), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert "demo-recipe" in result.stderr
    assert "demo_hook" in result.stderr
    assert json.loads(result.stdout) == {
        "action": "created",
        "name": "demo",
        "source": str(pack),
        "rev": None,
        "commit": None,
        "detail": None,
    }
    assert (library_dir() / "packs" / "demo").exists()

    again = run(["packs", "add", str(pack), "--force", "-f", "pipe"])
    assert again.exit_code == 0, again.output
    envelope = json.loads(again.stdout)
    assert envelope["kind"] == "recipe.add_outcome"
    assert envelope["record"]["action"] == "updated"


def test_add_hooked_pack_without_lock_leads_with_error_no_summary(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    (pack / "uv.lock").unlink()

    result = run(["packs", "add", str(pack)])

    assert result.exit_code == 1
    assert "pack project is missing uv.lock" in result.stderr
    # UX rider: validation leads; the pack summary never precedes the error.
    assert "Pack: demo" not in result.stderr
    assert "demo-recipe" not in result.stderr
    assert not (library_dir() / "packs" / "demo").exists()


def test_edit_uses_shared_editor_and_reports_bad_quoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "'unclosed")

    result = run(["packs", "edit", "demo"])

    # A broken $EDITOR is the environment's to fix.
    assert result.exit_code == 4, result.output
    assert "error: invalid quoting in $VISUAL or $EDITOR" in result.stderr


def test_add_force_fails_fast_on_local_edits_before_confirm(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    result = run(["packs", "add", str(pack)])
    assert result.exit_code == 0, result.output
    installed_recipe = library_dir() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = run(["packs", "add", str(pack), "--force"])

    assert result.exit_code == 1
    assert "pack 'demo' has local edits in the library" in result.stderr
    assert "--discard-edits" in result.stderr
    assert "Pack: demo" not in result.stderr
    assert installed_recipe.read_text().startswith("version: 1\ndescription: 'edited'")


def test_add_force_discard_edits_warns_in_preview_and_overwrites(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    run(["packs", "add", str(pack)])
    installed_recipe = library_dir() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = run(
        ["packs", "add", str(pack), "--force", "--discard-edits"],
    )

    assert result.exit_code == 0, result.output
    assert "warning: library copy has local edits; --discard-edits will overwrite them" in (
        result.stderr
    )
    assert installed_recipe.read_text() == "version: 1\nsteps: []\n"

    result = run(["packs", "add", str(pack), "--force"])
    assert result.exit_code == 0, result.output


def test_remove_warns_on_local_edits_before_confirm(
    tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    result = run(["packs", "add", str(pack)])
    assert result.exit_code == 0, result.output
    installed_recipe = library_dir() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")
    terminal.answers(False)

    result = run(["packs", "remove", "demo"])

    assert result.exit_code == 1, result.output
    assert "About to remove 1 pack:\n  - demo\n" in result.stderr
    assert (
        "warning: pack 'demo' has local edits in the library (via an edit or init "
        "command); removing discards them"
    ) in result.stderr
    assert "cancelled; no changes made" in result.stderr
    assert result.stdout == ""
    assert terminal.asked == [("confirm", "Remove 1 pack?")]
    assert (library_dir() / "packs" / "demo").exists()


def test_remove_dry_run_previews_without_removing(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0

    result = run(["packs", "remove", "demo", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    planned = {
        "action": "planned",
        "name": "demo",
        "source": str(pack),
        "rev": None,
        "commit": None,
        "detail": None,
    }
    assert json.loads(result.stdout) == [planned]
    assert (library_dir() / "packs" / "demo").exists()

    refused = run(["packs", "remove", "demo"])
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert (library_dir() / "packs" / "demo").exists()

    removed = run(["packs", "remove", "demo", "--yes", "-f", "pipe"])
    assert removed.exit_code == 0, removed.output
    envelope = json.loads(removed.stdout)
    assert envelope["kind"] == "recipe.remove_outcome"
    assert envelope["record"] == {**planned, "action": "removed"}
    assert not (library_dir() / "packs" / "demo").exists()


def test_remove_failure_is_a_failed_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0

    def _refuse(self: PackLibrary, name: str) -> None:
        raise OSError(f"permission denied: {name}")

    monkeypatch.setattr(PackLibrary, "remove", _refuse)
    result = run(["packs", "remove", "demo", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert result.failures == [("demo", "permission denied: demo")]
    [row] = json.loads(result.stdout)
    assert (row["name"], row["action"], row["source"]) == ("demo", "failed", str(pack))
    assert row["detail"] == "permission denied: demo"
    assert row["error"]["category"] == "failed"
    assert row["error"]["message"] == row["detail"]


def test_remove_that_cannot_update_the_index_is_partial_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0

    def _refuse(path: Path, text: str) -> None:
        raise OSError("read-only file system")

    with monkeypatch.context() as patched:
        patched.setattr(pack_store_module, "atomic_write", _refuse)
        result = run(["packs", "remove", "demo", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["action"] == "partial"
    assert "could not update packs.toml: read-only file system" in row["detail"]
    assert not (library_dir() / "packs" / "demo").exists()
    retried = run(["packs", "remove", "demo", "--yes"])
    assert retried.exit_code == 0, retried.output
    assert "demo" not in (library_dir() / "packs.toml").read_text()


def test_remove_repeated_name_reports_the_second_as_failed(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    assert run(["packs", "add", str(pack)]).exit_code == 0

    result = run(["packs", "remove", "demo", "demo", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert [row["action"] for row in json.loads(result.stdout)] == ["removed", "failed"]


def test_remove_rejects_index_rows_without_content_hash(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    result = run(["packs", "add", str(pack)])
    assert result.exit_code == 0, result.output
    index_path = library_dir() / "packs.toml"
    index_path.write_text(
        index_path.read_text(encoding="utf-8").replace("content_hash", "ignored_field"),
        encoding="utf-8",
    )

    result = run(["packs", "remove", "demo", "--yes"])

    assert result.exit_code != 0
    assert "content_hash" in result.output
    assert (library_dir() / "packs" / "demo").exists()


def test_remove_yes_skips_local_edits_warning(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)
    result = run(["packs", "add", str(pack)])
    assert result.exit_code == 0, result.output
    installed_recipe = library_dir() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = run(["packs", "remove", "demo", "--yes"])

    assert result.exit_code == 0, result.output
    assert "local edits" not in result.stderr
    assert not (library_dir() / "packs" / "demo").exists()


def test_add_rejects_rev_for_local_path_source(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_pack_project(pack)

    result = run(["packs", "add", str(pack), "--rev", "v1"])

    assert result.exit_code == 2
    assert "--rev is only valid for git URL sources" in result.stderr
    assert not (library_dir() / "packs" / "demo").exists()


def test_get_pack_and_recipe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    install_pack(source)

    pack = run(["packs", "get", "ansible", "--format", "json"])
    recipe = run(["get", "ansible/playbook", "--format", "json"])

    assert pack.exit_code == 0, pack.output
    assert json.loads(pack.stdout)["name"] == "ansible"
    assert recipe.exit_code == 0, recipe.output
    assert json.loads(recipe.stdout)["ref"] == "ansible/playbook"


def test_packs_list_and_get_name_an_aliased_packs_project_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_pack(source, manifest_name="upstream", recipes={"ci": "recipes/ci.yml"})
    install_pack(source, name="acme")

    listed = run(["packs", "list", "--format", "json"])
    shown = run(["packs", "get", "acme", "--format", "json"])

    assert listed.exit_code == 0, listed.output
    assert shown.exit_code == 0, shown.output
    row, detail = json.loads(listed.stdout)[0], json.loads(shown.stdout)
    assert row == {
        "name": "acme",
        "project": "untaped-recipe-upstream",
        "version": "0.1.0",
        "path": str(library_dir() / "packs" / "acme"),
        "source": str(source),
        "rev": "",
        "commit": "",
        "recipes": 1,
        "hooks": 0,
        "manifest_name": "upstream",
    }
    # One recipe.pack schema: get differs only in summarising recipes and hooks.
    assert detail == row | {"recipes": [{"name": "ci", "description": ""}], "hooks": []}
