"""CLI tests for apply, hook runs, pack libraries, ref resolution, and backups."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

import untaped.capabilities.recipe.infrastructure.file_writer as file_writer_module
from untaped import bootstrap
from untaped.capabilities.recipe import SPEC
from untaped.capabilities.recipe.builtins.registry import BUILTIN_HOOKS, BuiltinHook
from untaped.capabilities.recipe.cli import app
from untaped.capabilities.recipe.cli.common import library_root
from untaped.capabilities.recipe.domain.paths import is_path_ref
from untaped.capabilities.recipe.domain.plan import FileChange
from untaped.capabilities.recipe.infrastructure.backup import BackupDraft, BackupStore
from untaped.capabilities.recipe.infrastructure.pack_store import PackLibrary
from untaped.settings import get_settings
from untaped.testing import CliInvoker, ScriptedPromptBackend, assert_destructive_contract

pytestmark = pytest.mark.usefixtures("isolate_config")


_OUT_RECIPE = (
    "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
)


def _out_recipe(root: Path, template: str = "hello\n") -> tuple[Path, Path]:
    """Write ``recipe.yml`` rendering ``template.txt`` to ``out.txt`` plus a ``target`` dir."""
    (root / "recipe.yml").write_text(_OUT_RECIPE)
    (root / "template.txt").write_text(template)
    target = root / "target"
    target.mkdir()
    return root / "recipe.yml", target


def _write_hook_project(
    root: Path,
    *,
    public_name: str,
    module_name: str,
    code: str,
    package: str = "recipe_hooks",
) -> None:
    module_path = root / "src" / package / "hooks" / f"{module_name}.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    module_path.write_text(code)
    recipe_metadata = (
        '[tool.untaped_recipe.recipes]\n"demo" = { path = "recipe.yml" }\n\n'
        if (root / "recipe.yml").is_file()
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        f"{recipe_metadata}"
        "[tool.untaped_recipe.hooks]\n"
        f'"{public_name}" = {{ module = "{package}.hooks.{module_name}" }}\n'
    )
    subprocess.run(["uv", "lock"], cwd=root, check=True)


def _ctx(project: Path, target: Path) -> list[str]:
    """``hook run`` options naming the hook project and target directory."""
    return ["--project", str(project), "--target", str(target)]


def _write_pack_project(root: Path) -> None:
    hook_module = root / "src" / "demo_hooks" / "hooks" / "check.py"
    hook_module.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / "demo_hooks" / "__init__.py").write_text("")
    (root / "src" / "demo_hooks" / "hooks" / "__init__.py").write_text("")
    hook_module.write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
    )
    recipe = root / "recipes" / "demo" / "recipe.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"demo-recipe" = { path = "recipes/demo/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"demo_hook" = { module = "demo_hooks.hooks.check" }\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


def test_add_pack_installs_without_prompting_and_prints_summary(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)

    result = CliInvoker().invoke(app, ["add", str(pack), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert "demo-recipe" in result.stderr
    assert "demo_hook" in result.stderr
    assert json.loads(result.stdout) == {
        "action": "created",
        "name": "demo",
        "source": str(pack),
        "rev": None,
    }
    assert (library_root() / "packs" / "demo").exists()

    again = CliInvoker().invoke(app, ["add", str(pack), "--force", "-f", "pipe"])
    assert again.exit_code == 0, again.output
    envelope = json.loads(again.stdout)
    assert envelope["kind"] == "recipe.add_outcome"
    assert envelope["record"]["action"] == "updated"


def _write_hookless_pack_project(root: Path, *, lock: bool = True) -> None:
    recipe = root / "recipes" / "seed" / "recipe.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-hygiene"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"seed" = { path = "recipes/seed/recipe.yml" }\n'
    )
    if lock:
        (root / "uv.lock").write_text("version = 1\n")


def test_add_and_check_hookless_pack_without_lock(tmp_path: Path) -> None:
    # Boundary population: a hookless pack with no uv.lock passes check and adds.
    pack = tmp_path / "pack"
    _write_hookless_pack_project(pack, lock=False)

    check = CliInvoker().invoke(app, ["validate", str(pack)])
    assert check.exit_code == 0, check.output

    added = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert added.exit_code == 0, added.output
    assert (library_root() / "packs" / "hygiene").exists()


def test_add_hooked_pack_without_lock_leads_with_error_no_summary(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    (pack / "uv.lock").unlink()

    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])

    assert result.exit_code == 1
    assert "pack project is missing uv.lock" in result.stderr
    # UX rider: validation leads; the pack summary never precedes the error.
    assert "Pack: demo" not in result.stderr
    assert "demo-recipe" not in result.stderr
    assert not (library_root() / "packs" / "demo").exists()


def test_edit_uses_shared_editor_and_reports_bad_quoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    assert CliInvoker().invoke(app, ["add", str(pack), "--yes"]).exit_code == 0
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "'unclosed")

    result = CliInvoker().invoke(app, ["edit", "demo"])

    assert result.exit_code == 1, result.output
    assert "error: invalid quoting in $VISUAL or $EDITOR" in result.stderr


def test_add_force_fails_fast_on_local_edits_before_confirm(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(app, ["add", str(pack), "--force", "--yes"])

    assert result.exit_code == 1
    assert "pack 'demo' has local edits in the library" in result.stderr
    assert "--discard-edits" in result.stderr
    assert "Pack: demo" not in result.stderr
    assert installed_recipe.read_text().startswith("version: 1\ndescription: 'edited'")


def test_add_force_discard_edits_warns_in_preview_and_overwrites(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(
        app,
        ["add", str(pack), "--force", "--discard-edits", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "warning: library copy has local edits; --discard-edits will overwrite them" in (
        result.stderr
    )
    assert installed_recipe.read_text() == "version: 1\nsteps: []\n"

    result = CliInvoker().invoke(app, ["add", str(pack), "--force", "--yes"])
    assert result.exit_code == 0, result.output


def test_remove_warns_on_local_edits_before_confirm(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack)])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(app, ["remove", "demo"], interactive=True, prompt_backend=backend)

    assert result.exit_code == 1, result.output
    assert "About to remove 1 pack:\n  - demo\n" in result.stderr
    assert (
        "warning: pack 'demo' has local edits in the library (via edit or init "
        "recipe/hook); removing discards them"
    ) in result.stderr
    assert "cancelled; no changes made" in result.stderr
    assert backend.calls == [("confirm", "Continue?")]
    assert (library_root() / "packs" / "demo").exists()


def test_remove_dry_run_previews_without_removing(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    assert CliInvoker().invoke(app, ["add", str(pack)]).exit_code == 0

    result = CliInvoker().invoke(app, ["remove", "demo", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"action": "planned", "name": "demo", "source": None, "rev": None}
    ]
    assert (library_root() / "packs" / "demo").exists()

    refused = CliInvoker().invoke(app, ["remove", "demo"])
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert (library_root() / "packs" / "demo").exists()

    removed = CliInvoker().invoke(app, ["remove", "demo", "--yes", "-f", "pipe"])
    assert removed.exit_code == 0, removed.output
    envelope = json.loads(removed.stdout)
    assert envelope["kind"] == "recipe.remove_outcome"
    assert envelope["record"]["action"] == "removed"
    assert not (library_root() / "packs" / "demo").exists()


def test_remove_rejects_index_rows_without_content_hash(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    index_path = library_root() / "packs.toml"
    index_path.write_text(
        index_path.read_text(encoding="utf-8").replace("content_hash", "ignored_field"),
        encoding="utf-8",
    )

    result = CliInvoker().invoke(app, ["remove", "demo", "--yes"])

    assert result.exit_code != 0
    assert "content_hash" in result.output
    assert (library_root() / "packs" / "demo").exists()


def test_remove_yes_skips_local_edits_warning(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(app, ["remove", "demo", "--yes"])

    assert result.exit_code == 0, result.output
    assert "local edits" not in result.stderr
    assert not (library_root() / "packs" / "demo").exists()


def test_apply_yes_writes_and_emits_json_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=api",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api\n"
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "applied"
    assert "Recipe preview:" in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "Recipe apply:" in result.stderr


def test_apply_dry_run_defaults_to_table_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "planned"
    assert "Recipe preview:" in result.stderr
    assert "path" in result.stderr
    assert "action" in result.stderr
    assert "changes" in result.stderr
    assert "files_changed" not in result.stderr
    assert "error" not in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "create" in result.stderr
    assert "+1 -0" in result.stderr
    assert "--- a/out.txt" not in result.stderr
    assert "+++ b/out.txt" not in result.stderr


def test_apply_table_preview_counts_prefix_like_diff_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: remove\n"
        "    file: doc.yml\n"
        "  - type: template\n"
        "    template: plus.txt\n"
        "    dest: plus.txt\n"
    )
    (tmp_path / "plus.txt").write_text("++same\n++same\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "doc.yml").write_text("---\nold\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert str(target / "doc.yml") in result.stderr
    assert "+0 -2" in result.stderr
    assert str(target / "plus.txt") in result.stderr
    assert "+2 -0" in result.stderr


def test_apply_preview_diff_preserves_patch_headers(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path, "hello")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--preview", "diff"],
    )

    assert result.exit_code == 0, result.output
    assert "Recipe preview:" in result.stderr
    assert f"# {target}" in result.stderr
    # A created file diffs from /dev/null, and a missing final newline is
    # marked so the output applies with `patch`/`git apply`.
    assert "--- /dev/null\n+++ b/out.txt\n" in result.stderr
    assert "+hello\n\\ No newline at end of file\n" in result.stderr
    assert str(target / "out.txt") not in result.stderr


@pytest.mark.parametrize("preview", ["table", "diff"])
@pytest.mark.parametrize("case", ["change", "sensitive", "error"])
def test_apply_preview_renders_each_row_kind_with_absolute_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preview: str,
    case: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipes = {
        "change": _OUT_RECIPE,
        "sensitive": _OUT_RECIPE.replace(
            "steps:", "inputs:\n  token: {type: str, sensitive: true, required: true}\nsteps:"
        ),
        "error": "version: 1\nsteps:\n  - type: validate\n    hook: noop\n",
    }
    Path("recipe.yml").write_text(recipes[case])
    Path("template.txt").write_text("token={{ token }}\n" if case == "sensitive" else "hello\n")
    Path("target").mkdir()
    extra = ["--var", "token=secret"] if case == "sensitive" else []

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            "./recipe.yml",
            "target",
            "--dry-run",
            "--preview",
            preview,
            "-f",
            "json",
            *extra,
        ],
    )

    target = tmp_path / "target"
    assert result.exit_code == (1 if case == "error" else 0), result.output
    assert json.loads(result.stdout)[0]["action"] == ("failed" if case == "error" else "planned")
    assert "Recipe preview:" in result.stderr
    assert str(target) in result.stderr
    if case == "change" and preview == "table":
        assert str(target / "out.txt") in result.stderr
        assert "+1 -0" in result.stderr
    elif case == "change":
        assert f"# {target}" in result.stderr
        assert "# target" not in result.stderr
        assert "--- /dev/null\n+++ b/out.txt\n" in result.stderr
    elif case == "sensitive":
        # Sensitive targets collapse to a files_changed row in every preview mode.
        assert "files_changed" in result.stderr
        assert "out.txt" not in result.stderr
        assert "secret" not in result.stderr
    else:
        assert "noop" in result.stderr
        assert "+++ b/" not in result.stderr


def test_apply_preview_none_keeps_summary_and_stdout_format_independent(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        [
            *("apply", str(recipe), str(target), "--dry-run", "--preview", "none"),
            *("--format", "json", "--columns", "target_path"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"target_path": str(target)}]
    assert "Recipe preview:" in result.stderr
    assert "out.txt" not in result.stderr


def test_apply_quiet_mutes_preview_summary_and_post_run_info(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)
    root = bootstrap.build_root_app(builtins=(SPEC,), externals=())

    result = CliInvoker().invoke(
        root.meta,
        ["--quiet", "recipe", "apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Recipe preview:" not in result.stderr
    assert "Recipe dry run:" not in result.stderr


def test_apply_table_preview_uses_configured_collection_view(
    tmp_path: Path,
    isolate_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    isolate_config.write_text("profiles:\n  default:\n    ui:\n      collection_view: list\n")
    get_settings.cache_clear()
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "path:" in result.stderr
    assert "action: create" in result.stderr
    assert str(target / "out.txt") in result.stderr


def test_apply_table_preview_uses_configured_preview_max_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "500")
    monkeypatch.setenv("UNTAPED_RECIPE__PREVIEW_MAX_ROWS", "1")
    get_settings.cache_clear()
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert str(target) in result.stderr
    assert "files" in result.stderr
    assert "one.txt" not in result.stderr
    assert "two.txt" not in result.stderr


def test_apply_decline_reprints_summary_adjacent_to_prompt_without_writing(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--preview", "table", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )

    # A declined confirmation exits 1 with the standard decline line.
    assert result.exit_code == 1, result.output
    assert backend.calls == [("confirm", "Continue?")]
    assert [row["action"] for row in json.loads(result.stdout)] == ["cancelled"]
    assert result.stderr.rstrip().endswith("cancelled; no changes made")
    before_decline = result.stderr.rsplit("cancelled; no changes made", maxsplit=1)[0]
    assert before_decline.rstrip().endswith(
        "Recipe preview: 1 target, 1 changing, 0 unchanged, 0 failed, 2 files changed"
    )
    assert before_decline.count("Recipe preview:") == 2
    assert not (target / "one.txt").exists()
    assert not (target / "two.txt").exists()


def test_apply_preview_only_modes_and_noninteractive_default_write_nothing(
    tmp_path: Path,
) -> None:
    recipe, target = _out_recipe(tmp_path)

    dry_run = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])
    check = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--check"])
    declined = CliInvoker().invoke(app, ["apply", str(recipe), str(target)])

    assert dry_run.exit_code == 0, dry_run.output
    assert check.exit_code == 3, check.output
    assert "cancelled; no changes made" not in dry_run.stderr + check.stderr
    assert declined.exit_code != 0
    assert "requires --yes" in declined.output
    assert not (target / "out.txt").exists()


@pytest.mark.parametrize("rollback_fails", [True, False])
def test_apply_keeps_backup_only_when_write_rollback_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollback_fails: bool,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (recipe_dir / "one.txt").write_text("one-after\n")
    (recipe_dir / "two.txt").write_text("two-after\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "one.txt").write_text("one-before\n")
    (target / "two.txt").write_text("two-before\n")
    original_replace = file_writer_module.os.replace

    def fail_second_write(source: Path, dest: Path) -> None:
        if rollback_fails and ".rollback." in Path(source).name:
            raise OSError("rollback denied")
        if Path(dest).name == "two.txt":
            raise OSError("write failed")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_write)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir / "recipe.yml"), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0, result.output
    store = BackupStore(library_root() / "backups")
    if not rollback_fails:
        assert store.list() == []
        return
    # The partially-written target keeps its backup so it can be restored.
    assert "rollback incomplete" in result.stdout
    assert len(store.list()) == 1
    metadata = store.metadata("latest")
    assert [entry["relative_path"] for entry in metadata["files"]] == ["one.txt", "two.txt"]


@pytest.mark.parametrize("preview", ["table", "diff"])
def test_apply_check_explicit_preview_reports_drift_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preview: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--preview", preview],
    )

    assert result.exit_code == 3, result.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_root() / "backups").list() == []
    assert "Recipe preview:" in result.stderr
    if preview == "table":
        assert str(target / "out.txt") in result.stderr
        assert "changes" in result.stderr
    else:
        assert "--- /dev/null\n+++ b/out.txt\n" in result.stderr


def test_confirm_accept_applies_changes(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--preview", "none"],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(confirms=[True]),
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text(encoding="utf-8") == "hello\n"
    assert "cancelled; no changes made" not in result.stderr


def test_apply_stdin_confirms_on_the_controlling_terminal(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)
    backend = ScriptedPromptBackend(confirms=[True])

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--preview", "none"],
        input=f"{target}\n",
        terminal=True,
        prompt_backend=backend,
    )

    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Continue?")]
    assert (target / "out.txt").read_text(encoding="utf-8") == "hello\n"


def test_apply_check_reports_drift_without_writing_or_backing_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe, target = _out_recipe(tmp_path)

    drift = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert drift.exit_code == 3, drift.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_root() / "backups").list() == []
    rows = json.loads(drift.stdout)
    assert rows[0]["action"] == "planned"
    assert rows[0]["files_changed"] == 1
    assert "Recipe preview:" in drift.stderr
    assert str(target / "out.txt") not in drift.stderr
    assert "action" not in drift.stderr
    assert "changes" not in drift.stderr

    (target / "out.txt").write_text("hello\n")
    clean = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert clean.exit_code == 0, clean.output
    rows = json.loads(clean.stdout)
    assert rows[0]["action"] == "unchanged"
    assert rows[0]["files_changed"] == 0
    assert "Recipe preview:" in clean.stderr
    assert str(target / "out.txt") not in clean.stderr


def test_apply_stdin_requires_yes_and_resolves_workspace_repo_pipe(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {"path": str(workspace), "target_path": str(repo), "repo": "api"},
        }
    )

    refused = CliInvoker().invoke(app, ["apply", str(recipe), "--stdin"], input=payload + "\n")
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )
    assert result.exit_code == 0, result.output
    assert (repo / "out.txt").read_text() == "hello\n"


def test_apply_stdin_rejects_workspace_repo_pipe_without_target_path(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {"path": str(workspace), "repo": "api"},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )

    assert result.exit_code != 0
    assert "workspace.repo pipe record requires target_path" in result.output
    assert not (workspace / "out.txt").exists()
    assert not (repo / "out.txt").exists()


def test_apply_stdin_summary_only_is_noop(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.summary",
            "record": {
                "workspace": "prod",
                "path": str(workspace),
                "default_branch": "main",
                "repo_count": 0,
                "repo": "",
                "url": "",
                "repo_branch": None,
                "target_branch": None,
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Recipe apply: 0 applied, 0 unchanged, 0 failed" in result.stderr
    assert not (workspace / "out.txt").exists()


def test_apply_stdin_empty_input_still_errors(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input="",
    )

    assert result.exit_code != 0
    assert "no targets received on stdin" in result.output


def test_apply_check_allows_stdin_without_yes(tmp_path: Path) -> None:
    recipe, target = _out_recipe(tmp_path)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--check"],
        input=str(target) + "\n",
    )

    assert result.exit_code == 3, result.output
    assert "requires --yes" not in result.output
    assert not (target / "out.txt").exists()


def test_apply_stdin_without_yes_refuses_before_hooks_run(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    marker = tmp_path / "hook-ran"
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: touch\n"
        "    args:\n"
        f"      marker: {marker}\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="touch",
        module_name="touch",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    Path(args['marker']).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    refused = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), "--stdin"],
        input=str(target) + "\n",
    )

    assert refused.exit_code == 2
    assert "error: apply requires --yes when not interactive" in refused.output
    assert not marker.exists()


@pytest.mark.parametrize(
    ("recipe_content", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        ("version: 1\nname: demo\nsteps: []\n", "name is not allowed here"),
    ],
)
def test_apply_recipe_load_errors_are_reported_cleanly(
    tmp_path: Path,
    recipe_content: str,
    expected: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(recipe_content)
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: " in result.output
    assert expected in result.output
    # Domain errors name the file and never leak pydantic internals.
    assert str(recipe) in result.output
    assert "extra_forbidden" not in result.output
    assert "Traceback" not in result.output


def test_apply_missing_recipe_is_reported_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", "missing", str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: recipe not found: missing" in result.output
    assert "Traceback" not in result.output


def test_apply_var_values_keep_equals_and_unknown_vars_are_rejected(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "service=api=v1", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api=v1\n"

    rejected = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "servcie=typo", "--yes"],
    )
    assert rejected.exit_code != 0
    assert "unknown input" in rejected.output


@pytest.mark.parametrize("value", ["[a, b]", "a: b", "{x: y}", "true"])
def test_apply_scalar_var_values_that_look_like_yaml_stay_literal_strings(
    tmp_path: Path,
    value: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / value.replace("/", "_")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", f"service={value}", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == f"service={value}\n"


_COLS = "cols: {type: list, items: str, required: true}"


@pytest.mark.parametrize(
    ("declared", "args", "expected"),
    [
        pytest.param(
            "replicas: {type: int, required: true}",
            ["--var", "replicas=x"],
            "input 'replicas': cannot coerce value to int",
            id="scalar-coercion-error-names-input",
        ),
        pytest.param(
            "replicas: {type: int, required: true}",
            ["--var", "replicas=[a, b]"],
            "cannot coerce value to int",
            id="scalar-error-ignores-yaml-look",
        ),
        pytest.param(_COLS, ["--var", "cols=[a, b]"], {"cols": ["a", "b"]}, id="yaml-list"),
        pytest.param(
            _COLS, ["--var", "cols=["], "input 'cols' expects YAML list:", id="malformed-yaml"
        ),
        pytest.param(
            _COLS,
            ["--var", "cols=enabled"],
            "input 'cols' expects YAML list: parsed value is not a list",
            id="yaml-scalar-for-list",
        ),
        pytest.param(
            _COLS, ["--vars-file", "{vars_file}"], {"cols": ["a", "b"]}, id="vars-file-native"
        ),
        pytest.param(
            "tokens: {type: list, sensitive: true, required: true}",
            ["--var", "tokens=[alpha, beta]"],
            {"tokens": "***"},
            id="sensitive-structured-redacted-whole",
        ),
    ],
)
def test_apply_var_values_parse_by_declared_type(
    tmp_path: Path,
    declared: str,
    args: list[str],
    expected: str | dict[str, object],
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(f"version: 1\ninputs:\n  {declared}\nsteps: []\n")
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text("cols:\n  - a\n  - b\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            *("apply", str(recipe), str(target), "--yes", "--format", "json"),
            *(arg.format(vars_file=vars_file) for arg in args),
        ],
    )

    if isinstance(expected, dict):
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)[0]["inputs"] == expected
        assert "alpha" not in result.stdout
    else:
        assert result.exit_code != 0
        assert expected in result.stderr
        assert "Traceback" not in result.output
        if "int" in declared:
            assert "expects YAML" not in result.output


@pytest.mark.parametrize(
    ("vars_file", "extra", "code", "message"),
    [
        (None, [], 1, "--vars-file file not found"),
        ("[unclosed\n", [], 1, "--vars-file file is invalid YAML"),
        ("- a\n", [], 1, "--vars-file file must contain a YAML mapping"),
        ("{}\n", ["--hook-timeout", "-1"], 2, "--hook-timeout must be greater than or equal to 0"),
    ],
)
def test_apply_rejects_bad_vars_file_and_hook_timeout(
    tmp_path: Path, vars_file: str | None, extra: list[str], code: int, message: str
) -> None:
    recipe, target = _out_recipe(tmp_path)
    path = tmp_path / "vars.yml"
    if vars_file is not None:
        path.write_text(vars_file)

    result = CliInvoker().invoke(
        app, ["apply", str(recipe), str(target), "--vars-file", str(path), *extra, "--dry-run"]
    )

    assert result.exit_code == code, result.output
    assert message in result.stderr
    assert "Traceback" not in result.output


def test_apply_derives_target_inputs_and_redacts_outcome_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from: '{{ target.name }}'\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api secret\n"
    rows = json.loads(result.stdout)
    assert rows[0]["inputs"] == {"service": "api", "token": "***"}
    assert "secret" not in result.stdout


def test_apply_sensitive_target_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    sensitive: true\n"
        "    required: true\n"
        "    from: '{{ record.token }}'\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "recipe.target",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["error"] == "input 'token': cannot coerce value to int"
    assert rows[0]["inputs"] == {}


def test_apply_sensitive_global_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", f"token={secret}", "--dry-run"],
    )

    assert result.exit_code != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "cannot coerce value to int" in result.output


def test_apply_sensitive_inputs_redact_warnings_and_suppress_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: leak\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (recipe_dir / "template.txt").write_text("token={{ token }}\n")
    _write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "import json\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    helpers.warn(json.dumps({'warning': inputs['token']}))\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "diff suppressed for target with sensitive inputs" not in result.stderr
    assert str(target) in result.stderr
    assert "files_changed" in result.stderr
    assert "out.txt" not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == ["diagnostic suppressed for target with sensitive inputs"]
    assert rows[0]["inputs"] == {"token": "***"}


def _write_skip_recipe_project(tmp_path: Path) -> Path:
    recipe_dir = tmp_path / "scoped"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n"
        "  - type: validate\n    hook: scope\n"
        "  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (recipe_dir / "template.txt").write_text("applied\n")
    _write_hook_project(
        recipe_dir,
        public_name="scope",
        module_name="scope",
        code=(
            "def validate(*, inputs, target, args, helpers):\n"
            "    if (target / 'wanted.txt').exists():\n"
            "        return helpers.pass_()\n"
            "    return helpers.skip('out of scope')\n"
        ),
    )
    return recipe_dir


def test_apply_skip_verdict_end_to_end(tmp_path: Path) -> None:
    recipe_dir = _write_skip_recipe_project(tmp_path)
    in_target = tmp_path / "app-in"
    in_target.mkdir()
    (in_target / "wanted.txt").write_text("x")
    out_target = tmp_path / "app-out"
    out_target.mkdir()
    backups = BackupStore(library_root() / "backups")

    # Boundary population: every target skips -> exit 0, no writes, no backup.
    all_skip = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(out_target), "--yes", "--format", "json"],
    )
    assert all_skip.exit_code == 0, all_skip.output
    assert json.loads(all_skip.stdout)[0]["action"] == "skipped"
    assert not (out_target / "out.txt").exists()
    assert backups.list() == []
    assert "1 skipped" in all_skip.stderr

    # --check: a skip is success, never counted as drift.
    checked = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(out_target), "--check", "--format", "json"],
    )
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.stdout)[0]["action"] == "skipped"

    # Mixed run: applicable target applies (and is backed up), other skips.
    mixed = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe_dir),
            str(in_target),
            str(out_target),
            "--yes",
            "--format",
            "json",
        ],
    )
    assert mixed.exit_code == 0, mixed.output
    rows = {row["target_path"]: row for row in json.loads(mixed.stdout)}
    assert rows[str(in_target)]["action"] == "applied"
    assert rows[str(out_target)]["action"] == "skipped"
    assert (in_target / "out.txt").read_text() == "applied\n"
    assert not (out_target / "out.txt").exists()
    assert len(backups.list()) == 1
    assert "1 applied" in mixed.stderr
    assert "1 skipped" in mixed.stderr

    # Pipe record carries the new skipped status.
    piped = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(out_target), "--dry-run", "--format", "pipe"],
    )
    assert piped.exit_code == 0, piped.output
    record = json.loads(piped.stdout)
    assert record["kind"] == "recipe.apply_outcome"
    assert record["record"]["action"] == "skipped"

    # Skip flows through --stdin targets too.
    from_stdin = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), "--stdin", "--yes", "--format", "json"],
        input=f"{out_target}\n",
    )
    assert from_stdin.exit_code == 0, from_stdin.output
    assert json.loads(from_stdin.stdout)[0]["action"] == "skipped"


def test_apply_transform_warn_reaches_outcome(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "noted"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: transform\n    file: data.txt\n    hook: note\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="note",
        module_name="note",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    helpers.warn('noticed something')\n"
            "    helpers.warn('and another')\n"
            "    return content + 'noted\\n'\n"
        ),
    )
    target = tmp_path / "app"
    target.mkdir()
    (target / "data.txt").write_text("start\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    warnings = json.loads(result.stdout)[0]["warnings"]
    assert "noticed something" in warnings
    assert "and another" in warnings


def test_apply_sensitive_inputs_redact_hook_failures(
    tmp_path: Path,
) -> None:
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: config.txt\n"
        "    hook: leak\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    raise RuntimeError(f\"failed {inputs['token']!r}\")\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()
    (target / "config.txt").write_text("before\n")

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["error"] == (
        "target planning failed; diagnostic suppressed for target with sensitive inputs"
    )
    assert rows[0]["inputs"] == {"token": "***"}


@pytest.mark.parametrize(
    ("declared", "args", "expected"),
    [
        pytest.param(
            "replicas: {type: int, scope: target}",
            ["--var", "replicas=not-an-int"],
            "cannot coerce value to int",
            id="invalid-fixed-target-input",
        ),
        pytest.param(
            "service: {type: str, from: '{{ target.name'}",
            [],
            "invalid input source expression for service",
            id="invalid-jinja-source",
        ),
        pytest.param(
            "service: {type: str, from: '{% for item in [target.name] %}{{ item }}{% endfor %}'}",
            [],
            "invalid input source expression for service",
            id="jinja-control-block",
        ),
    ],
)
def test_apply_invalid_inputs_fail_before_target_rows(
    tmp_path: Path, declared: str, args: list[str], expected: str
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(f"version: 1\ninputs:\n  {declared}\nsteps: []\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(first), str(second), *args, "--dry-run", "--format", "json"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert expected in result.stderr


def test_apply_outcome_includes_plan_warnings_in_every_format(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: missing.yml\n"
        "    optional: true\n"
        "    hook: unused\n"
        "  - type: remove\n"
        "    globs:\n"
        "      - '**/*.generated'\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    warnings = [
        "optional transform skipped missing file: missing.yml",
        "globs matched no files: **/*.generated",
    ]

    def run(fmt: str) -> str:
        result = CliInvoker().invoke(
            app, ["apply", str(recipe), str(target), "--dry-run", "--format", fmt]
        )
        assert result.exit_code == 0, result.output
        return result.stdout

    assert json.loads(run("json"))[0]["warnings"] == warnings
    assert "- 'optional transform skipped missing file: missing.yml'" in run("yaml")
    pipe_row = json.loads(run("pipe"))
    assert pipe_row["kind"] == "recipe.apply_outcome"
    assert pipe_row["record"]["warnings"] == warnings


def test_apply_table_inputs_cell_renders_key_value_pairs_not_dict_repr(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service:\n    type: str\n  cols:\n    type: list\nsteps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            *("apply", str(recipe), str(target), "--yes", "--columns", "inputs"),
            *("--var", "service=api", "--var", "cols=[a, b]"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "service=api, cols=['a', 'b']" in result.stdout
    assert "{'service'" not in result.stdout


def test_apply_bulk_invocation_backs_up_only_successful_targets_in_one_bundle(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: config.txt\n"
    )
    (tmp_path / "template.txt").write_text("after\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    for target in (first, second):
        target.mkdir()
        (target / "config.txt").write_text("before\n")
    missing = tmp_path / "missing"

    result = CliInvoker().invoke(
        app,
        [
            *("apply", str(recipe), str(first), str(second), str(missing)),
            *("--yes", "--format", "json"),
        ],
    )

    assert result.exit_code != 0
    assert [row["action"] for row in json.loads(result.stdout)] == ["applied", "applied", "failed"]
    assert (first / "config.txt").read_text() == "after\n"
    store = BackupStore(library_root() / "backups")
    [bundle] = store.list()
    assert len(store.metadata(bundle.id)["files"]) == 2


def test_apply_record_valued_source_fails_without_copying_record_contents(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  debug: {type: str, from: '{{ record }}'}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "recipe.target",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert rows[0]["error"] == "derived input value must be a scalar"


def test_apply_outcome_inputs_render_in_yaml_and_table(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    yaml_result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "yaml"],
    )
    assert yaml_result.exit_code == 0, yaml_result.output
    assert "inputs:" in yaml_result.stdout
    assert "service: api" in yaml_result.stdout

    table_result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])
    assert table_result.exit_code == 0, table_result.output
    assert "inputs" in table_result.stdout
    assert "service" in table_result.stdout


def test_apply_derives_inputs_from_pipe_record_and_input_from_override(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from:\n"
        "      - '{{ record.repo }}'\n"
        "      - '{{ target.name }}'\n"
        "  owner:\n"
        "    type: str\n"
        "    from: '{{ record.team }}'\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ owner }}\n")
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "repo": "api",
                "team": "platform",
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            "--stdin",
            "--yes",
            "--input-from",
            "owner={{ target.parent_name }}",
            "--format",
            "pipe",
        ],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api workspace\n"
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.apply_outcome"
    assert row["record"]["inputs"] == {"service": "api", "owner": "workspace"}


def test_apply_derives_structured_input_from_pipe_record(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  collections:\n"
        "    type: list\n"
        "    items: str\n"
        "    from: '{{ record.collections }}'\n"
        "steps: []\n"
    )
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "collections": ["ansible.builtin", "community.general"],
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes", "--format", "pipe"],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.apply_outcome"
    assert row["record"]["inputs"] == {"collections": ["ansible.builtin", "community.general"]}
    assert next(iter(row["record"])) == "target_path"


def test_apply_rejects_input_from_conflicts_global_scope_and_interactive_check(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  owner: {type: str, scope: global, required: true}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    conflict = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=fixed",
            "--input-from",
            "service={{ target.name }}",
            "--var",
            "owner=platform",
            "--dry-run",
        ],
    )
    assert conflict.exit_code != 0
    assert "cannot combine --var/--vars-file and --input-from for service" in conflict.output

    global_source = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--input-from",
            "owner={{ target.name }}",
            "--dry-run",
        ],
    )
    assert global_source.exit_code != 0
    assert "scope global" in global_source.output

    interactive_check = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--interactive", "--check"],
    )
    assert interactive_check.exit_code == 2
    assert "--interactive cannot be combined with --check" in interactive_check.output


def test_apply_stdin_interactive_without_tty_fails_before_prompting(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service: {type: str, scope: target, required: true}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--interactive", "--dry-run"],
        input=str(target) + "\n",
    )

    assert result.exit_code != 0
    assert "interactive input requires a terminal" in result.output


def test_apply_backup_metadata_records_redacted_per_target_inputs(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  token: {type: str, scope: global, sensitive: true, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()
    (target / "out.txt").write_text("before\n")

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = BackupStore(library_root() / "backups").metadata("latest")
    assert metadata["files"][0]["inputs"] == {"service": "api", "token": "***"}
    assert "secret" not in json.dumps(metadata)


def test_ansible_style_optional_multi_file_recipe_acceptance(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    files:\n"
        "      - local.yml\n"
        "      - site.yml\n"
        "      - playbooks/deploy.yml\n"
        "    optional: true\n"
        "    hook: add_play_collections\n"
        "  - type: remove\n"
        "    files:\n"
        "      - ansible.cfg\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="add_play_collections",
        module_name="add_play_collections",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '# collections added to ' + file.name + '\\n'\n"
        ),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "playbooks").mkdir()
    (first / "local.yml").write_text("- hosts: localhost\n")
    (first / "ansible.cfg").write_text("[defaults]\n")
    (second / "local.yml").write_text("- hosts: localhost\n")
    (second / "site.yml").write_text("- hosts: all\n")
    (second / "playbooks" / "deploy.yml").write_text("- hosts: deploy\n")
    (second / "ansible.cfg").write_text("[defaults]\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(first), str(second), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == [
        "optional transform skipped missing file: site.yml",
        "optional transform skipped missing file: playbooks/deploy.yml",
    ]
    assert rows[1]["warnings"] == []
    assert "# collections added to local.yml" in (first / "local.yml").read_text()
    assert not (first / "ansible.cfg").exists()
    assert "# collections added to local.yml" in (second / "local.yml").read_text()
    assert "# collections added to site.yml" in (second / "site.yml").read_text()
    assert "# collections added to deploy.yml" in (second / "playbooks" / "deploy.yml").read_text()
    assert not (second / "ansible.cfg").exists()


def test_explicit_single_file_recipe_does_not_use_sibling_hook_project(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: transform\n    file: local.yml\n    hook: sibling\n"
    )
    _write_hook_project(
        tmp_path,
        public_name="sibling",
        module_name="sibling",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'changed\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "hook not found: sibling" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_external_hook_args_with_yaml_dates_are_rejected_before_worker(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: local.yml\n"
        "    hook: stamp\n"
        "    args:\n"
        "      day: 2026-06-19\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="stamp",
        module_name="stamp",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'day=' + args['day'] + '\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "is not JSON-serializable" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_hook_run_accepts_path_ref_form_like_new_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise S1: `hook run ./pack/hook` must resolve like `new hook` does.
    project = tmp_path / "collections-ensure"
    project.mkdir()
    _write_hook_project(
        project,
        public_name="has_playbooks",
        module_name="has_playbooks",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    target = tmp_path / "app"
    target.mkdir()
    monkeypatch.chdir(tmp_path)

    ok = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "./collections-ensure/has_playbooks",
            "--target",
            str(target),
            "--format",
            "json",
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["status"] == "pass"

    conflict = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "./collections-ensure/has_playbooks",
            "--project",
            str(project),
            "--target",
            str(target),
        ],
    )
    assert conflict.exit_code == 2
    assert "not both" in conflict.output


def test_hook_run_transform_reads_disk_and_emits_exact_content(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="append",
        module_name="append",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('external diagnostic')\n"
            "    return content + args['suffix']\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "append",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--arg",
            "suffix='!'",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert (target / "local.txt").read_text() == "start"
    assert "external diagnostic" in result.stderr
    assert str(target) in result.stderr
    assert "local.txt" in result.stderr
    assert '"suffix": "!"' in result.stderr


def test_hook_run_transform_content_overrides_do_not_require_existing_file(
    tmp_path: Path,
) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="show_context",
        module_name="show_context",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '|' + file.name + '|' + target.name\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    content_file = tmp_path / "fixture.txt"
    content_file.write_text("from-file")

    literal = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "missing.txt",
            "--content",
            "literal",
        ],
    )
    stdin = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "stdin.txt",
            "--content",
            "-",
        ],
        input="from-stdin",
    )
    file_result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            *_ctx(hook_project, target),
            "--file",
            "file.txt",
            "--content-file",
            str(content_file),
        ],
    )

    assert literal.exit_code == 0, literal.output
    assert literal.stdout == "literal|missing.txt|target"
    assert stdin.exit_code == 0, stdin.output
    assert stdin.stdout == "from-stdin|stdin.txt|target"
    assert file_result.exit_code == 0, file_result.output
    assert file_result.stdout == "from-file|file.txt|target"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--content-file", "{missing}"], "error: --content-file file not found"),
        (["--arg", "items=["], "error: --arg value for 'items' is invalid YAML"),
    ],
)
def test_hook_run_bad_flag_values_are_reported_cleanly(
    tmp_path: Path, args: list[str], message: str
) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")
    extra = [arg.format(missing=tmp_path / "missing.txt") for arg in args]

    result = CliInvoker().invoke(
        app,
        ["hook", "run", "types", *_ctx(hook_project, target), "--file", "local.txt", *extra],
    )

    assert result.exit_code != 0
    assert message in result.output
    assert "Traceback" not in result.output


def test_hook_run_transform_diff_and_structured_output(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="replace",
        module_name="replace",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content.replace('old', 'new')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("old\n")

    diff = CliInvoker().invoke(
        app,
        ["hook", "run", "replace", *_ctx(hook_project, target), "--file", "local.txt", "--diff"],
    )
    structured = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "replace",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--diff",
            "--format",
            "json",
        ],
    )

    assert diff.exit_code == 0, diff.output
    assert "--- a/local.txt" in diff.stdout
    assert "+++ b/local.txt" in diff.stdout
    assert "-old" in diff.stdout
    assert "+new" in diff.stdout
    assert structured.exit_code == 0, structured.output
    row = json.loads(structured.stdout)
    assert row["content"] == "new\n"
    assert "--- a/local.txt" in row["diff"]
    assert "inputs" not in row
    assert "args" not in row


def test_hook_run_validate_records_and_fail_exit(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "def validate(*, inputs, target, args, helpers):\n"
            "    if args.get('fail'):\n"
            "        return helpers.fail('not ready')\n"
            "    helpers.warn('check manually')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    warn = CliInvoker().invoke(
        app,
        ["hook", "run", "ready", *_ctx(hook_project, target), "--format", "json"],
    )
    failed = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "ready",
            *_ctx(hook_project, target),
            "--arg",
            "fail=yes",
            "--format",
            "pipe",
        ],
    )

    assert warn.exit_code == 0, warn.output
    warn_row = json.loads(warn.stdout)
    assert warn_row["hook"] == "ready"
    assert warn_row["kind"] == "validate"
    # helpers.warn() is now an accumulator: the verdict passes and the
    # warning surfaces on the human channel (stderr).
    assert warn_row["status"] == "pass"
    assert warn_row["message"] == ""
    assert "check manually" in warn.stderr
    assert failed.exit_code == 1, failed.output
    failed_row = json.loads(failed.stdout)
    assert failed_row["kind"] == "recipe.hook_run"
    assert failed_row["record"]["status"] == "fail"
    assert failed_row["record"]["message"] == "not ready"


def test_hook_run_dual_export_infers_or_requires_kind(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="dual",
        module_name="dual",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return 'transformed'\n\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    helpers.warn('validated')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("before")

    inferred_transform = CliInvoker().invoke(
        app,
        ["hook", "run", "dual", *_ctx(hook_project, target), "--file", "local.txt"],
    )
    ambiguous = CliInvoker().invoke(
        app,
        ["hook", "run", "dual", *_ctx(hook_project, target)],
    )
    explicit_validate = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "dual",
            *_ctx(hook_project, target),
            "--kind",
            "validate",
            "--format",
            "json",
        ],
    )

    assert inferred_transform.exit_code == 0, inferred_transform.output
    assert inferred_transform.stdout == "transformed"
    assert ambiguous.exit_code != 0
    assert (
        "hook 'dual' exports both transform() and validate(); pass --kind or --file"
        in ambiguous.output
    )
    assert explicit_validate.exit_code == 0, explicit_validate.output
    row = json.loads(explicit_validate.stdout)
    assert row["kind"] == "validate"
    assert row["status"] == "pass"
    assert row["message"] == ""
    assert "validated" in explicit_validate.stderr


def test_hook_run_rejects_kind_specific_context_options(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    marker = tmp_path / "marker"
    _write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            f"    Path({str(marker)!r}).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    validate_with_file = CliInvoker().invoke(
        app,
        ["hook", "run", "ready", *_ctx(hook_project, target), "--file", "local.txt"],
    )
    validate_with_diff = CliInvoker().invoke(
        app,
        ["hook", "run", "ready", *_ctx(hook_project, target), "--diff"],
    )
    transform_without_file = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "yaml_edit",
            "--target",
            str(target),
            "--args",
            str(tmp_path / "missing.yml"),
        ],
    )

    assert validate_with_file.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_file.output
    assert validate_with_diff.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_diff.output
    assert not marker.exists()
    assert transform_without_file.exit_code != 0
    assert "transform hooks require --file" in transform_without_file.output


def test_hook_run_inputs_and_args_merge_files_and_yaml_flags(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return (\n"
            "        f\"enabled={inputs['enabled']!r};\"\n"
            "        f\"count={inputs['count']!r};\"\n"
            "        f\"mode={args['mode']!r}\"\n"
            "    )\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")
    inputs = tmp_path / "inputs.yml"
    inputs.write_text("enabled: false\ncount: 1\n")
    args = tmp_path / "args.yml"
    args.write_text("mode: old\n")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "types",
            *_ctx(hook_project, target),
            "--file",
            "local.txt",
            "--inputs",
            str(inputs),
            "--input",
            "enabled=yes",
            "--input",
            "count=3",
            "--args",
            str(args),
            "--arg",
            "mode=new",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "enabled=True;count=3;mode='new'"
    assert '"enabled": true' in result.stderr
    assert '"count": 3' in result.stderr
    assert '"mode": "new"' in result.stderr


def test_hook_run_explicit_project_must_be_valid_before_global_or_builtin_fallback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("enabled: false\n")
    args = tmp_path / "args.yml"
    args.write_text("edits:\n  - {op: set, path: [enabled], value: true}\n")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "yaml_edit",
            "--project",
            str(tmp_path / "missing-project"),
            "--target",
            str(target),
            "--file",
            "local.yml",
            "--args",
            str(args),
        ],
    )

    assert result.exit_code != 0
    assert "error: hook project not found" in result.output
    assert result.stdout == ""
    assert "Hook run:" not in result.stderr


def test_hook_run_never_adopts_cwd_project_without_explicit_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_project = tmp_path / "pack-source"
    _write_hook_project(
        global_project,
        public_name="shadow",
        module_name="shadow",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return 'global'\n"
        ),
    )
    PackLibrary(library_root=library_root()).add(
        global_project,
        source=str(global_project),
        rev=None,
        name="shared",
        force=False,
    )
    cwd_project = tmp_path / "cwd"
    _write_hook_project(
        cwd_project,
        public_name="shadow",
        module_name="shadow",
        code="def transform(content, *, inputs, target, file, args, helpers):\n    return 'cwd'\n",
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    monkeypatch.chdir(cwd_project)
    implicit = CliInvoker().invoke(
        app,
        ["hook", "run", "shadow", "--target", str(target), "--file", "local.txt"],
    )
    explicit = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "shadow",
            "--project",
            ".",
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )
    path_ref = CliInvoker().invoke(
        app,
        ["hook", "run", "../cwd/shadow", "--target", str(target), "--file", "local.txt"],
    )

    # The cwd's hook project is only used when named explicitly.
    assert implicit.exit_code == 0, implicit.output
    assert implicit.stdout == "global"
    assert explicit.exit_code == 0, explicit.output
    assert explicit.stdout == "cwd"
    assert path_ref.exit_code == 0, path_ref.output
    assert path_ref.stdout == "cwd"


def test_hook_run_builtin_is_not_shadowed_by_cwd_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd_project = tmp_path / "cloned"
    _write_hook_project(
        cwd_project,
        public_name="yaml_edit",
        module_name="evil",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return 'repo code ran'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.yml").write_text("a: 1\n")
    monkeypatch.chdir(cwd_project)

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "yaml_edit",
            "--target",
            str(target),
            "--file",
            "a.yml",
            "--arg",
            "edits=[{op: set, path: [a], value: 2}]",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "a: 2\n"


def test_hook_run_external_failure_prints_traceback(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="broken",
        module_name="broken",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('before failure')\n"
            "    raise RuntimeError('boom')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    result = CliInvoker().invoke(
        app,
        ["hook", "run", "broken", *_ctx(hook_project, target), "--file", "local.txt"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "before failure" in result.stderr
    assert "Traceback" in result.stderr
    assert "RuntimeError: boom" in result.stderr


def test_hook_run_builtin_stdout_is_redirected_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("debug_builtin")

    def transform(content: str, **kwargs: object) -> str:
        print("builtin diagnostic")
        return content + "!"

    module.transform = transform  # type: ignore[attr-defined]
    monkeypatch.setitem(
        BUILTIN_HOOKS,
        "debug_builtin",
        BuiltinHook(module=module, exports=frozenset({"transform"})),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = CliInvoker().invoke(
        app,
        ["hook", "run", "debug_builtin", "--target", str(target), "--file", "local.txt"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert "builtin diagnostic" in result.stderr


def test_hook_run_quiet_suppresses_context_but_not_hook_diagnostics(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="noisy",
        module_name="noisy",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('hook diagnostic')\n"
            "    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    root = bootstrap.build_root_app(builtins=(SPEC,), externals=())
    result = CliInvoker().invoke(
        root.meta,
        [
            "--quiet",
            "recipe",
            "hook",
            "run",
            "noisy",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start"
    assert "hook diagnostic" in result.stderr
    assert "Hook run:" not in result.stderr
    assert str(target) not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["get", "missing"],
        ["backup", "get", "latest"],
    ],
)
def test_library_command_value_errors_are_reported_cleanly(args: list[str]) -> None:
    result = CliInvoker().invoke(app, args)

    assert result.exit_code != 0
    assert "error: " in result.output
    assert "Traceback" not in result.output


def test_recipe_check_validates_package_assets_and_hooks(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("hello\n")
    (recipe_dir / "copy.txt").write_text("copy\n")
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
        "  - type: copy\n"
        "    source: copy.txt\n"
        "    dest: copy.txt\n"
        "  - type: validate\n"
        "    hook: check\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )

    result = CliInvoker().invoke(app, ["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "recipe-hooks",
            "status": "pass",
            "path": str(recipe_dir),
            "recipes": 1,
            "hooks": 1,
            "error": "",
        }
    ]
    assert "Recipe preview:" not in result.stderr


def test_recipe_check_rejects_step_hook_kind_mismatch(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: validate\n    hook: check\n"
    )
    package = recipe_dir / "src" / "recipe_hooks" / "hooks"
    package.mkdir(parents=True)
    (recipe_dir / "src" / "recipe_hooks" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "check.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    (recipe_dir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "recipe-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"demo" = { path = "recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"check" = { module = "recipe_hooks.hooks.check" }\n'
    )
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = CliInvoker().invoke(app, ["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "validate step hook 'check' does not export a validate() function" in rows[0]["error"]


@pytest.mark.parametrize(
    ("recipe_body", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        (
            "version: 1\n"
            "steps:\n"
            "  - type: template\n"
            "    template: missing.txt\n"
            "    dest: out.txt\n",
            "template not found",
        ),
        (
            "version: 1\nsteps:\n  - type: validate\n    hook: missing\n",
            "hook not found",
        ),
        (
            "version: 1\n"
            "inputs:\n"
            "  service: {type: str, from: '{{ target.name | upper }}'}\n"
            "steps: []\n",
            "invalid input source expression for service",
        ),
        (
            "version: 1\n"
            "inputs:\n"
            "  kind: {type: str, default: web}\n"
            "steps:\n"
            "  - type: template\n"
            "    template: 'missing/{{ kind }}.txt'\n"
            "    dest: out.txt\n",
            "template not found",
        ),
        (
            "version: 1\ninputs:\n  replicas: {type: int, default: many}\nsteps: []\n",
            "default: cannot coerce value to int",
        ),
        (
            "version: 1\ninputs:\n  replicas: {type: int, required: true, default: 2}\nsteps: []\n",
            "required input cannot declare a default",
        ),
    ],
)
def test_recipe_check_reports_invalid_packages(
    tmp_path: Path,
    recipe_body: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(recipe_body)

    result = CliInvoker().invoke(
        app,
        ["validate", str(recipe_dir / "recipe.yml"), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]
    assert "Traceback" not in result.output


@pytest.mark.parametrize("referenced", [True, False])
@pytest.mark.parametrize(
    ("breakage", "expected"),
    [
        ("lock", "missing uv.lock"),
        ("module", "hook module file not found"),
        ("runtime-dependency", "must not depend on untaped at runtime"),
    ],
)
def test_recipe_check_reports_broken_local_hook_projects(
    tmp_path: Path,
    referenced: bool,
    breakage: str,
    expected: str,
) -> None:
    # The local hook project is validated even when no step references it.
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    steps = "\n  - type: validate\n    hook: check\n" if referenced else " []\n"
    (recipe_dir / "recipe.yml").write_text(f"version: 1\nsteps:{steps}")
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    pyproject = recipe_dir / "pyproject.toml"
    if breakage == "lock":
        (recipe_dir / "uv.lock").unlink()
    elif breakage == "module":
        (recipe_dir / "src" / "recipe_hooks" / "hooks" / "check.py").unlink()
    else:
        pyproject.write_text(
            pyproject.read_text().replace("dependencies = []", 'dependencies = ["untaped>=4,<5"]')
        )

    result = CliInvoker().invoke(app, ["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]


def test_help_placeholders_and_ref_grammar_render_meaningfully(tmp_path: Path) -> None:
    invoker = CliInvoker()

    init_help = invoker.invoke(app, ["init", "--help"])
    hook_run_help = invoker.invoke(app, ["hook", "run", "--help"])
    apply_help = invoker.invoke(app, ["apply", "--help"])

    assert "PACK/RECIPE or PACK/HOOK reference" in init_help.stdout
    assert "PACK/HOOK reference." in hook_run_help.stdout
    for result in (init_help, hook_run_help):
        assert "REF  /." not in result.stdout
    assert "pack/recipe" in apply_help.stdout
    assert "pack:recipe" not in apply_help.stdout


@pytest.mark.parametrize(
    ("pyproject", "expected"),
    [
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"bad-name" = { module = "recipe_hooks.hooks.check" }\n',
            "invalid hook name",
        ),
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"check" = { module =',
            "invalid pack project pyproject",
        ),
    ],
)
def test_recipe_check_validates_unreferenced_local_hook_project_metadata(
    tmp_path: Path,
    pyproject: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (recipe_dir / "pyproject.toml").write_text(pyproject)
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = CliInvoker().invoke(app, ["validate", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]


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
        BackupStore(library_root() / "backups"),
        recipe_name="demo",
        inputs={"service": "api"},
        changes=[change],
    )
    config.write_text("after\n")
    return bundle, config


def test_backup_commands_show_list_and_restore(tmp_path: Path) -> None:
    bundle, config = _config_backup(tmp_path)
    invoker = CliInvoker()

    listed = invoker.invoke(app, ["backup", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)[0]["id"] == bundle.id
    shown = invoker.invoke(app, ["backup", "get", bundle.id])
    assert shown.exit_code == 0, shown.output
    assert "recipe: demo" in shown.stdout
    assert f"files:\n  - {config}" in shown.stdout
    assert "[{" not in shown.stdout
    shown_json = invoker.invoke(app, ["backup", "get", bundle.id, "--format", "json"])
    assert shown_json.exit_code == 0, shown_json.output
    assert json.loads(shown_json.stdout)["id"] == bundle.id

    refused = invoker.invoke(app, ["backup", "restore", bundle.id])
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert config.read_text() == "after\n"
    restored = invoker.invoke(app, ["backup", "restore", bundle.id, "--yes"])
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"


def test_backup_restore_dry_run_and_decline_change_nothing(tmp_path: Path) -> None:
    bundle, config = _config_backup(tmp_path)

    dry_run = CliInvoker().invoke(app, ["backup", "restore", bundle.id, "--dry-run"])
    declined = CliInvoker().invoke(
        app,
        ["backup", "restore", bundle.id],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )

    assert dry_run.exit_code == 0, dry_run.output
    assert "About to restore 1 file:" in dry_run.stderr
    # A declined confirmation exits 1 with the standard decline line.
    assert declined.exit_code == 1, declined.output
    assert "cancelled; no changes made" in declined.stderr
    assert "restored" not in declined.stderr
    assert config.read_text() == "after\n"


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
    store = BackupStore(library_root() / "backups")
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

    result = CliInvoker().invoke(app, ["backup", "restore", bundle.id, "--yes"])

    assert result.exit_code == 1, result.output
    assert "disk full" in result.output
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
    backups = library_root() / "backups"
    ids = {
        "a": "20200101T000000000000Z-aaaaaaaa",
        "b": "20990201T000000000000Z-bbbbbbbb",
        "c": "20990301T000000000000Z-cccccccc",
    }
    for bundle_id in ids.values():
        _seed_bundle(backups, bundle_id)
    if env_keep is not None:
        monkeypatch.setenv("UNTAPED_RECIPE__BACKUP_KEEP", env_keep)
        get_settings.cache_clear()

    result = CliInvoker().invoke(app, ["backup", "prune", *args, "--yes"])

    assert result.exit_code == 0, result.output
    assert {key for key, bundle_id in ids.items() if (backups / bundle_id).exists()} == survivors
    pruned = len(ids) - len(survivors)
    assert f"pruned {pruned} of 3 backups" in result.stderr
    assert "reclaimed" in result.stderr
    assert ids["a"] in result.stdout


def test_backup_prune_requires_a_policy(tmp_path: Path) -> None:
    _seed_bundle(library_root() / "backups", "20250101T000000000000Z-aaaaaaaa")

    result = CliInvoker().invoke(app, ["backup", "prune", "--yes"])

    assert result.exit_code != 0
    assert "backup prune needs --keep/--older-than" in result.output


def test_backup_prune_conforms_to_destructive_contract(tmp_path: Path) -> None:
    backups = library_root() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    new = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    def assert_unchanged() -> None:
        assert old.exists()
        assert new.exists()

    assert_destructive_contract(
        app,
        ["backup", "prune", "--keep", "1"],
        assert_unchanged=assert_unchanged,
    )


def test_init_rejects_hook_only_flags_for_packs_and_recipes(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["init", "pack", "demo", "--kind", "validate"])

    assert result.exit_code == 2
    assert "error: --kind and --force apply only to init hook" in result.stderr
    assert not (tmp_path / "demo").exists()


def test_renamed_commands_keep_deprecated_aliases(tmp_path: Path) -> None:
    root = bootstrap.build_root_app(externals=[])
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\nsteps: []\n")
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text("{}\n")
    target = tmp_path / "target"
    target.mkdir()

    checked = CliInvoker().invoke(root, ["recipe", "check", str(recipe), "-f", "json"])
    shown = CliInvoker().invoke(root, ["recipe", "show", "yaml_edit", "-f", "json"])
    applied = CliInvoker().invoke(
        root,
        ["recipe", "apply", str(recipe), str(target), "--vars", str(vars_file), "--dry-run"],
    )

    assert checked.exit_code == 0, checked.output
    assert "warning: `check` is deprecated" in checked.stderr
    assert "use `validate`" in checked.stderr
    assert shown.exit_code == 0, shown.output
    assert "use `get`" in shown.stderr
    assert applied.exit_code == 0, applied.output
    assert "use `--vars-file`" in applied.stderr
    backups = CliInvoker().invoke(root, ["recipe", "backup", "show", "latest"])
    assert "use `get`" in backups.stderr
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        scaffolded = CliInvoker().invoke(root, ["recipe", "new", "pack", "demo", "--no-lock"])
    assert scaffolded.exit_code == 0, scaffolded.output
    assert "use `init`" in scaffolded.stderr
    assert (tmp_path / "demo" / "pyproject.toml").is_file()


def test_apply_unchanged_targets_report_unchanged_status(tmp_path: Path) -> None:
    recipe, _ = _out_recipe(tmp_path)
    changing = tmp_path / "changing"
    changing.mkdir()
    unchanged = tmp_path / "unchanged"
    unchanged.mkdir()
    (unchanged / "out.txt").write_text("hello\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(changing), str(unchanged), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = {row["target_path"]: row["action"] for row in json.loads(result.stdout)}
    assert rows[str(changing)] == "applied"
    assert rows[str(unchanged)] == "unchanged"
    assert "planned" not in result.stdout
    assert "1 applied, 1 unchanged" in result.stderr


def test_backup_prune_counts_failed_deletions_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backups = library_root() / "backups"
    first = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    second = _seed_bundle(backups, "20250201T000000000000Z-bbbbbbbb")
    _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    original_delete = BackupStore.delete

    def flaky_delete(self: BackupStore, backup_id: str) -> None:
        if backup_id.endswith("aaaaaaaa"):
            raise ValueError(f"backup not found: {backup_id}")
        original_delete(self, backup_id)

    monkeypatch.setattr(BackupStore, "delete", flaky_delete)

    result = CliInvoker().invoke(app, ["backup", "prune", "--keep", "1", "--yes"])

    assert result.exit_code == 1, result.output
    assert "error: 20250101T000000000000Z-aaaaaaaa" in result.stderr
    assert first.exists()
    assert not second.exists()


def test_recipe_check_accepts_input_templated_asset_paths(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    (recipe_dir / "templates").mkdir(parents=True)
    (recipe_dir / "templates" / "web.txt").write_text("web\n")
    (recipe_dir / "files").mkdir()
    (recipe_dir / "files" / "web.cfg").write_text("cfg\n")
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  kind: {type: str, default: web}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: 'templates/{{ kind }}.txt'\n"
        "    dest: out.txt\n"
        "  - type: copy\n"
        "    source: 'files/{{ kind }}.cfg'\n"
        "    dest: out.cfg\n"
    )

    result = CliInvoker().invoke(
        app, ["validate", str(recipe_dir / "recipe.yml"), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_add_rejects_rev_for_local_path_source(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)

    result = CliInvoker().invoke(app, ["add", str(pack), "--rev", "v1", "--yes"])

    assert result.exit_code == 2
    assert "--rev is only valid for git URL sources" in result.stderr
    assert not (library_root() / "packs" / "demo").exists()


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


def _write_pack(
    root: Path,
    *,
    manifest_name: str,
    recipes: dict[str, str],
    hooks: dict[str, str] | None = None,
    recipe_body: str = "version: 1\nsteps: []\n",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    recipe_rows: list[str] = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(recipe_body, encoding="utf-8")
        recipe_rows.append(f'"{name}" = {{ path = "{relative}" }}')
    hook_rows: list[str] = []
    for name, module in (hooks or {}).items():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(
            "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
            encoding="utf-8",
        )
        package = root / "src" / Path(module.split(".")[0])
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        hooks_package = root / "src" / Path(*module.split(".")[:-1])
        (hooks_package / "__init__.py").write_text("", encoding="utf-8")
        hook_rows.append(f'"{name}" = {{ module = "{module}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        + "\n".join(recipe_rows)
        + "\n"
        + ("\n[tool.untaped_recipe.hooks]\n" + "\n".join(hook_rows) + "\n" if hook_rows else ""),
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _install_pack(source: Path, *, name: str | None = None) -> None:
    PackLibrary(library_root=library_root()).add(
        source,
        source=str(source),
        rev=None,
        name=name,
        force=False,
    )


def test_check_unknown_bare_ref_keeps_recipe_miss(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["validate", "not_a_builtin", "--format", "json"])

    assert result.exit_code == 1
    assert "recipe not found: not_a_builtin" in result.stderr


def test_show_prefers_library_hook_over_builtin(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="shadow",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"yaml_edit": "shadow_pack.hooks.yaml_edit"},
    )
    _install_pack(source)

    result = CliInvoker().invoke(app, ["get", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["module"] == "shadow_pack.hooks.yaml_edit"


def test_edit_rejects_builtin_hook(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["edit", "yaml_edit"])

    assert result.exit_code == 1
    assert "built-in hooks are engine-owned and cannot be edited: yaml_edit" in result.stderr


def test_unified_show_pack_and_recipe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)

    pack = CliInvoker().invoke(app, ["get", "ansible", "--format", "json"])
    recipe = CliInvoker().invoke(app, ["get", "ansible/playbook", "--format", "json"])

    assert pack.exit_code == 0, pack.output
    assert json.loads(pack.stdout)["name"] == "ansible"
    assert recipe.exit_code == 0, recipe.output
    assert json.loads(recipe.stdout)["ref"] == "ansible/playbook"


def test_unified_check_pack_validates_recipe_hook_exports(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
        recipe_body="version: 1\nsteps:\n  - type: validate\n    hook: check\n",
    )
    _install_pack(source)

    result = CliInvoker().invoke(app, ["validate", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_check_flags_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    (source / "tests" / "playbook" / "basic" / "given").mkdir(parents=True)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    _install_pack(source)

    result = CliInvoker().invoke(app, ["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert row["error"] == "tests directory names no known recipe: renamed"


def test_check_reports_stale_lockfile_for_hook_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)

    def _stale(project_root: Path) -> None:
        raise ValueError(f"lockfile is stale — run 'uv lock' in {project_root}")

    monkeypatch.setattr("untaped.capabilities.recipe.infrastructure.pack_files.check_lock", _stale)
    result = CliInvoker().invoke(app, ["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert "lockfile is stale — run 'uv lock' in" in row["error"]


def test_check_hook_pack_without_lock_keeps_pack_error_exact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)
    installed = library_root() / "packs" / "ansible"
    (installed / "uv.lock").unlink()

    result = CliInvoker().invoke(app, ["validate", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["error"] == f"pack project is missing uv.lock: {installed}"


def test_check_without_ref_reports_library_reconcile_and_pack_rows(tmp_path: Path) -> None:
    good_source = tmp_path / "good-source"
    stale_source = tmp_path / "stale-source"
    _write_pack(good_source, manifest_name="good", recipes={"ok": "recipes/ok.yml"})
    _write_pack(stale_source, manifest_name="stale", recipes={"old": "recipes/old.yml"})
    _install_pack(good_source, name="good")
    _install_pack(stale_source, name="stale")
    shutil.rmtree(library_root() / "packs" / "stale")
    _write_pack(
        library_root() / "packs" / "orphan",
        manifest_name="orphan",
        recipes={"playbook": "recipes/playbook.yml"},
    )

    result = CliInvoker().invoke(app, ["validate", "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    errors = {row["error"] for row in rows if row["status"] == "error"}
    assert errors == {
        "pack 'stale' is in packs.toml but missing from packs/",
        "pack directory 'orphan' is not recorded in packs.toml",
    }
    passes = {row["pack"] for row in rows if row["status"] == "pass"}
    assert passes == {"good", "orphan"}


def test_apply_bare_ref_uses_library_even_when_matching_local_directory_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ansible" / "playbook").mkdir(parents=True)
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", "ansible/playbook", str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "recipe not found" not in result.output


def test_apply_explicit_and_yaml_paths_load_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    recipe_dir = tmp_path / "a" / "b"
    recipe_dir.mkdir(parents=True)
    recipe = recipe_dir / "recipe.yml"
    recipe.write_text("version: 1\nsteps: []\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    explicit_dir = CliInvoker().invoke(app, ["apply", "./a/b/recipe.yml", str(target), "--dry-run"])
    yaml_suffix = CliInvoker().invoke(app, ["apply", "a/b/recipe.yml", str(target), "--dry-run"])

    assert explicit_dir.exit_code == 0, explicit_dir.output
    assert yaml_suffix.exit_code == 0, yaml_suffix.output


def test_cli_emit_kinds_are_the_surviving_pack_unification_set() -> None:
    allowed = {
        "recipe.add_outcome",
        "recipe.sync_outcome",
        "recipe.apply_outcome",
        "recipe.remove_outcome",
        "recipe.backup",
        "recipe.hook_run",
        "recipe.recipe",
        "recipe.hook",
        "recipe.pack",
        "recipe.check",
        "recipe.test",
    }
    cli_dir = Path(__file__).parents[3] / "src" / "untaped" / "capabilities" / "recipe" / "cli"
    found: set[str] = set()
    for path in cli_dir.glob("*.py"):
        found.update(re.findall(r'kind="(recipe\.[^"]+)"', path.read_text(encoding="utf-8")))

    assert found == allowed


def _install_good_and_broken_packs(tmp_path: Path) -> None:
    good = tmp_path / "good"
    _write_pack(good, manifest_name="good", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(good)
    broken = tmp_path / "broken"
    _write_pack(broken, manifest_name="broken", recipes={"other": "recipes/other.yml"})
    _install_pack(broken)
    installed = library_root() / "packs" / "broken" / "pyproject.toml"
    installed.write_text("[project\nname = ", encoding="utf-8")


def test_list_skips_unparsable_pack_with_warning(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    result = CliInvoker().invoke(app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert [row["ref"] for row in json.loads(result.stdout)] == ["good/playbook"]
    assert "broken" in result.stderr
    assert "warning" in result.stderr.lower()


def test_check_reports_error_row_for_unparsable_pack(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    result = CliInvoker().invoke(app, ["validate", "--format", "json"])

    rows = {row["pack"]: row for row in json.loads(result.stdout)}
    assert rows["good"]["status"] == "pass"
    assert rows["broken"]["status"] == "error"
    assert "pyproject" in rows["broken"]["error"]
    # the TOML parse detail is included, not just the file path
    assert "line" in rows["broken"]["error"]
    assert result.exit_code != 0


def test_resolution_ignores_unparsable_pack_unless_named(tmp_path: Path) -> None:
    _install_good_and_broken_packs(tmp_path)

    shown = CliInvoker().invoke(app, ["get", "playbook", "--format", "json"])
    named = CliInvoker().invoke(app, ["get", "broken"])
    qualified = CliInvoker().invoke(app, ["get", "broken/other"])

    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["ref"] == "good/playbook"
    for result in (named, qualified):
        assert result.exit_code != 0
        assert "broken" in result.stderr
        assert "line" in result.stderr


def test_path_ref_helper_classifies_dot_forms() -> None:
    for value in (".", "..", "./x", "../x", "/abs", "~/x", "~"):
        assert is_path_ref(value), value
    for value in ("pack", "pack/recipe", "x.yml", ".hidden"):
        assert not is_path_ref(value), value


_BINARY = b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary \x80\r\n"


def test_apply_copies_and_removes_binary_files_byte_exact(tmp_path: Path) -> None:
    pack = tmp_path / "mypack"
    _write_pack(
        pack,
        manifest_name="mypack",
        recipes={"fix": "recipes/fix.yml"},
        recipe_body=(
            "version: 1\nsteps:\n"
            "  - type: copy\n    source: logo.png\n    dest: assets/logo.png\n"
            "  - type: remove\n    file: old.png\n"
        ),
    )
    (pack / "recipes" / "logo.png").write_bytes(_BINARY)
    target = tmp_path / "target"
    target.mkdir()
    (target / "old.png").write_bytes(_BINARY[::-1])

    diff = CliInvoker().invoke(
        app, ["apply", str(pack), str(target), "--preview", "diff", "--dry-run"]
    )
    applied = CliInvoker().invoke(app, ["apply", str(pack), str(target), "--yes"])

    assert diff.exit_code == 0, diff.output
    assert "Binary file assets/logo.png differs" in diff.stderr
    assert applied.exit_code == 0, applied.output
    assert (target / "assets" / "logo.png").read_bytes() == _BINARY
    assert not (target / "old.png").exists()

    restored = CliInvoker().invoke(app, ["backup", "restore", "latest", "--yes"])

    assert restored.exit_code == 0, restored.output
    assert (target / "old.png").read_bytes() == _BINARY[::-1]
    assert not (target / "assets" / "logo.png").exists()


@pytest.mark.parametrize("installed", [True, False])
def test_apply_pack_with_several_recipes_asks_for_one(tmp_path: Path, installed: bool) -> None:
    source = tmp_path / "acme"
    _write_pack(
        source,
        manifest_name="acme",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    if installed:
        _install_pack(source)
    target = tmp_path / "target"
    target.mkdir()

    ref = "acme" if installed else str(source)
    result = CliInvoker().invoke(app, ["apply", ref, str(target), "--yes"])

    assert result.exit_code != 0
    assert "pack 'acme' has 2 recipes" in result.stderr
    assert "acme/one" in result.stderr
    assert "acme/two" in result.stderr


def test_unified_list_recipes_hooks_and_packs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)

    recipes = CliInvoker().invoke(app, ["list", "--format", "json"])
    hooks = CliInvoker().invoke(app, ["list", "--hooks", "--format", "json"])
    packs = CliInvoker().invoke(app, ["list", "--packs", "--format", "json"])

    assert recipes.exit_code == 0, recipes.output
    assert json.loads(recipes.stdout) == [
        {
            "pack": "ansible",
            "name": "playbook",
            "ref": "ansible/playbook",
            "path": str(library_root() / "packs" / "ansible" / "recipes/playbook/recipe.yml"),
        }
    ]
    # Library hooks list before the built-ins.
    assert [row["ref"] for row in json.loads(hooks.stdout)] == ["ansible/check", "yaml_edit"]
    assert json.loads(packs.stdout)[0]["name"] == "ansible"


def test_list_hooks_shows_builtins_even_on_empty_library(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--hooks", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "(builtin)",
            "name": "yaml_edit",
            "ref": "yaml_edit",
            "module": "untaped.capabilities.recipe.builtins.hooks.yaml_edit",
            "path": rows[0]["path"],
        }
    ]
    assert rows[0]["path"].endswith("yaml_edit.py")
    assert "no packs installed" not in result.stderr


@pytest.mark.parametrize("args", [["list"], ["list", "--packs"], ["validate"]])
def test_empty_library_prints_guidance(tmp_path: Path, args: list[str]) -> None:
    table = CliInvoker().invoke(app, args)
    as_json = CliInvoker().invoke(app, [*args, "--format", "json"])

    assert table.exit_code == 0, table.output
    assert table.stdout == ""
    assert "no packs installed" in table.stderr
    assert "untaped recipe init pack NAME" in table.stderr
    assert "add" in table.stderr
    assert as_json.exit_code == 0, as_json.output
    # validate without a ref never enumerates the built-ins; list keeps
    # machine formats free of the human hint.
    assert json.loads(as_json.stdout) == []
    assert ("no packs installed" in as_json.stderr) is (args[0] == "validate")


def test_builtin_hook_get_and_validate_render_detail_and_pass_row(tmp_path: Path) -> None:
    shown = CliInvoker().invoke(app, ["get", "yaml_edit", "--format", "json"])
    checked = CliInvoker().invoke(app, ["validate", "yaml_edit", "--format", "json"])

    assert shown.exit_code == 0, shown.output
    detail = json.loads(shown.stdout)
    assert detail["ref"] == "yaml_edit"
    assert detail["module"] == "untaped.capabilities.recipe.builtins.hooks.yaml_edit"
    assert "transform" in detail["exports"]
    assert checked.exit_code == 0, checked.output
    rows = json.loads(checked.stdout)
    assert rows == [{"recipe": "yaml_edit", "status": "pass", "path": rows[0]["path"], "error": ""}]
    assert rows[0]["path"].endswith("yaml_edit.py")


@pytest.mark.parametrize("shadow", ["pack", "recipe"])
def test_check_prefers_library_refs_over_builtin(tmp_path: Path, shadow: str) -> None:
    source = tmp_path / "source"
    if shadow == "pack":
        _write_pack(source, manifest_name="yaml_edit", recipes={"playbook": "recipes/p.yml"})
        _install_pack(source, name="yaml_edit")
    else:
        _write_pack(source, manifest_name="shadow", recipes={"yaml_edit": "recipes/yaml.yml"})
        _install_pack(source)

    result = CliInvoker().invoke(app, ["validate", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    if shadow == "pack":
        assert (row["pack"], row["path"]) == ("yaml_edit", str(library_root() / "packs/yaml_edit"))
    else:
        assert (row["recipe"], row["path"]) == (
            "shadow/yaml_edit",
            str(library_root() / "packs" / "shadow" / "recipes/yaml.yml"),
        )


@pytest.mark.parametrize(("hooks", "probes"), [({"check": "ansible_pack.hooks.check"}, 1), ({}, 0)])
def test_check_probes_lock_freshness_once_per_hook_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hooks: dict[str, str],
    probes: int,
) -> None:
    source = tmp_path / "source"
    recipes = {name: f"recipes/{name}/recipe.yml" for name in ("one", "two", "three")}
    _write_pack(source, manifest_name="ansible", recipes=recipes, hooks=hooks)
    _install_pack(source)
    probed: list[Path] = []
    monkeypatch.setattr(
        "untaped.capabilities.recipe.infrastructure.pack_files.check_lock", probed.append
    )

    result = CliInvoker().invoke(app, ["validate", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(probed) == probes


def test_check_hookless_pack_without_lock_passes_every_ref_form(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="plain", recipes={"ok": "recipes/ok.yml"})
    _install_pack(source)
    installed = library_root() / "packs" / "plain"
    (installed / "uv.lock").unlink()
    (source / "uv.lock").unlink()
    pack_row = {
        "pack": "plain",
        "status": "pass",
        "path": str(installed),
        "recipes": 1,
        "hooks": 0,
        "error": "",
    }

    def validate(*ref: str) -> list[dict[str, object]]:
        result = CliInvoker().invoke(app, ["validate", *ref, "--format", "json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    assert validate("plain") == [pack_row]
    assert validate() == [pack_row]
    assert validate("plain/ok") == [
        {
            "recipe": "plain/ok",
            "status": "pass",
            "path": str(installed / "recipes/ok.yml"),
            "error": "",
        }
    ]
    assert validate(str(source)) == [{**pack_row, "path": str(source)}]


@pytest.mark.parametrize(
    ("ref", "make_dir", "expected", "hint"),
    [
        ("demo", True, "recipe not found: demo", "a path named 'demo' exists"),
        ("demo", False, "recipe not found: demo", None),
        ("foo bar", False, "not found: foo bar", None),
    ],
)
def test_library_miss_hints_only_at_an_existing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    make_dir: bool,
    expected: str,
    hint: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    if make_dir:
        (tmp_path / ref).mkdir()

    result = CliInvoker().invoke(app, ["get", ref])

    assert result.exit_code == 1
    assert expected in result.stderr
    assert "safe library name" not in result.stderr
    if hint is None:
        assert "a path named" not in result.stderr
    else:
        assert hint in result.stderr
        assert "prefix ./" in result.stderr


@pytest.mark.parametrize(
    ("ref", "installed", "expected", "hinted"),
    [
        ("./demo", True, "recipe file not found", True),
        ("./demo.yml", True, "recipe file not found: demo.yml", True),
        ("./demo", False, "recipe file not found", False),
        ("./my recipe.yml", True, "recipe file not found: my recipe.yml", False),
        ("./nope.yml", False, "recipe file not found: nope.yml", False),
    ],
)
def test_explicit_path_miss_hints_at_matching_library_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    installed: bool,
    expected: str,
    hinted: bool,
) -> None:
    if installed:
        source = tmp_path / "source"
        _write_pack(source, manifest_name="pack", recipes={"demo": "recipes/demo.yml"})
        _install_pack(source)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    applied = CliInvoker().invoke(app, ["apply", ref, str(tmp_path), "--yes"])

    assert applied.exit_code == 1
    assert expected in applied.stderr
    assert ("did you mean the library ref 'demo'?" in applied.stderr) is hinted
    assert "safe library name" not in applied.stderr
    if ref.endswith(".yml"):
        checked = CliInvoker().invoke(app, ["validate", ref, "--format", "json"])
        assert checked.exit_code == 1
        assert expected in checked.stderr
        assert "Traceback" not in applied.output + checked.output


@pytest.mark.parametrize(
    ("ref", "installed"),
    [(".", False), ("./", False), ("{pack}", False), ("acme", True)],
)
def test_apply_pack_ref_picks_its_only_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    installed: bool,
) -> None:
    pack = tmp_path / "acme"
    _write_pack(pack, manifest_name="acme", recipes={"fix": "recipes/fix.yml"})
    if installed:
        _install_pack(pack)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.chdir(pack)
    args = [ref.format(pack=pack), str(target), "--yes", "--format", "json"]
    if ref.startswith("."):
        args += ["--recipe", "fix"]

    result = CliInvoker().invoke(app, ["apply", *args])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["recipe"] == "acme/fix"
